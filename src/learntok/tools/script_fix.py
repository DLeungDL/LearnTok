#!/usr/bin/env python3
"""script_fix.py — 確定性後處理層（deterministic post-processing）

品質規則的「程式兜底層」：LLM 生成的腳本在此做機械式修正，目標是通過
validate_script.py 的 0 錯誤 / 0 警告標準。本模組不呼叫 LLM，只做
格式／說話者／佔比修正，不改內容事實；terms 的 source 永不在此被發明。

用法：
  python -m learntok.tools.script_fix --self-test           # 內建案例自測
  python -m learntok.tools.script_fix --fix script.json     # 修正腳本（原地）
"""
import argparse
import json
import os
import re
import sys

from learntok import config

MAX_LEN = 25
MIN_LEN = 8
A_MIN = 30.0
A_MAX = 38.0
BUILD_DIR = config.build_dir()
_BOUNDS = set("。！？!?；;，,、")


def _is_question(text):
    return (text or "").rstrip().endswith(("？", "?"))


def _last_boundary(text, start, end):
    """在 [start, end) 內從後往前找最後一個斷句/子句邊界，回傳切點（不含）。"""
    for i in range(end - 1, start - 1, -1):
        if text[i] in _BOUNDS:
            return i + 1
    return -1


def split_text(text, max_len=MAX_LEN, min_len=MIN_LEN):
    """把一段話拆成 [min_len, max_len] 的片段，盡量停在句子/子句邊界。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    pieces = []
    start = 0
    n = len(text)
    while n - start > max_len:
        end = start + max_len
        # 只在 [start+min_len, end) 找邊界，避免拆出 < min_len 的頭段
        cut = _last_boundary(text, start + min_len, end)
        if cut <= start:
            cut = end
        pieces.append(text[start:cut])
        start = cut
    pieces.append(text[start:])
    # 合併 < min_len 的殘句到前一句
    merged = []
    for p in pieces:
        if merged and len(p) < min_len and len(merged[-1]) + len(p) <= max_len:
            merged[-1] += p
        else:
            merged.append(p)
    # 最後一段仍 < min_len：從前一段往前找邊界補齊（沒有邊界時逐字搬移）
    while len(merged) >= 2 and len(merged[-1]) < min_len:
        prev = merged[-2]
        if len(prev) + len(merged[-1]) <= max_len:
            merged[-2] = prev + merged[-1]
            merged.pop()
            continue
        cut = -1
        for i in range(len(prev) - 1, -1, -1):
            if prev[i] in _BOUNDS:
                cut = i + 1
                break
        if cut <= 0:
            need = min_len - len(merged[-1])
            if len(prev) - need >= min_len:
                merged[-2] = prev[:-need]
                merged[-1] = prev[-need:] + merged[-1]
                continue
            break
        overflow = prev[cut:]
        if cut <= 0 or not overflow:
            need = min_len - len(merged[-1])
            if len(prev) - need >= min_len:
                merged[-2] = prev[:-need]
                merged[-1] = prev[-need:] + merged[-1]
                continue
            break
        merged[-2] = prev[:cut]
        merged[-1] = overflow + merged[-1]
        if len(merged[-2]) < min_len:
            break
    return merged


def split_long_lines(lines):
    changed = False
    out = []
    for ln in lines:
        t = ln.get("text", "")
        if len(t) <= MAX_LEN:
            out.append(ln)
            continue
        pieces = split_text(t)
        if len(pieces) <= 1:
            out.append(ln)
            continue
        changed = True
        for i, p in enumerate(pieces):
            new = dict(ln)
            new["text"] = p
            if i > 0:
                new.pop("terms", None)
            if i < len(pieces) - 1:
                new.pop("_sfx", None)
            out.append(new)
    return out, changed


def _replacement_ok(out, start, end, repl):
    """把 out[start:end] 換成 repl 後，鄰近區域是否產生 3 連相同 speaker。"""
    new = out[:start] + repl + out[end:]
    n = len(new)
    for idx in range(max(1, start - 1), min(n - 2, start + len(repl) + 1) + 1):
        if (new[idx - 1].get("speaker") == new[idx].get("speaker")
                == new[idx + 1].get("speaker")):
            return False
    return True


def fix_short_lines(lines):
    """把 < 8 字的行併入相鄰行（優先併入下一行，其次前一行），合併後仍 <= 25 字。"""
    out = list(lines)
    changed = False
    i = 0
    while i < len(out):
        t = out[i].get("text", "")
        if len(t) >= MIN_LEN:
            i += 1
            continue
        nxt = out[i + 1] if i + 1 < len(out) else None
        prev = out[i - 1] if i > 0 else None
        # 合併對象：優先下一行，其次前一行；合併後若超過 25 字就拆句，
        # 保證每片都在 8~25 字（過長與過短一次解決）。
        target = None
        direction = None
        if nxt is not None:
            target, direction = nxt, "next"
        elif prev is not None:
            target, direction = prev, "prev"
        if target is None:
            i += 1
            continue
        merged_text = t + target["text"] if direction == "next" else target["text"] + t
        pieces = split_text(merged_text) if len(merged_text) > MAX_LEN else [merged_text]
        repl = [dict(target, text=piece) for piece in pieces]
        if direction == "next":
            out[i:i + 2] = repl
        else:
            out[i - 1:i + 1] = repl
        changed = True
    return out, changed


def _would_create_run(lines, idx, speaker):
    prev = lines[idx - 1].get("speaker") if idx > 0 else None
    nxt = lines[idx + 1].get("speaker") if idx + 1 < len(lines) else None
    return prev == speaker and nxt == speaker


def fix_speaker_streaks(lines):
    """同一 speaker 最多連續 2 行：優先合併相鄰同 speaker 的短句，否則改中段說話者。"""
    out = list(lines)
    changed = False
    while True:
        n = len(out)
        run_start = run_end = None
        i = 0
        while i < n:
            j = i
            while j + 1 < n and out[j + 1].get("speaker") == out[i].get("speaker"):
                j += 1
            if j - i + 1 >= 3:
                run_start, run_end = i, j
                break
            i = j + 1
        if run_start is None:
            break
        merged = False
        for k in range(run_start, run_end):
            a = out[k].get("text", "")
            b = out[k + 1].get("text", "")
            if len(a) + len(b) <= MAX_LEN:
                new = dict(out[k])
                new["text"] = a + b
                if out[k + 1].get("_sfx"):
                    new["_sfx"] = out[k + 1]["_sfx"]
                del out[k + 1]
                changed = True
                merged = True
                break
        if not merged:
            mid = run_start + (run_end - run_start) // 2
            other = "A" if out[mid].get("speaker") == "B" else "B"
            out[mid]["speaker"] = other
            changed = True
    return out, changed


def _a_ratio(lines):
    n = len(lines)
    if not n:
        return 0.0
    a = sum(1 for ln in lines if ln.get("speaker") == "A")
    return a / n * 100.0


def fix_a_ratio(lines, lo=A_MIN, hi=A_MAX):
    """A 佔比目標 30~38%。

    - 過高：把「非問句」的 A 台詞直接改為 B（避免 3 連 B）；全被夾住時改為
      併入相鄰 B（合併 <= 25 字）。
    - 過低：把 B 問句優先改為 A（避免 3 連 A），沒有問句時改一般 B 台詞。
    """
    out = list(lines)
    changed = False
    guard = 0
    while guard < 100:
        n = len(out)
        ratio = _a_ratio(out)
        if lo <= ratio <= hi:
            break
        done = False
        if ratio > hi:
            for i, ln in enumerate(out):
                if ln.get("speaker") != "A" or _is_question(ln.get("text", "")):
                    continue
                if not _would_create_run(out, i, "B"):
                    out[i]["speaker"] = "B"
                    changed = True
                    done = True
                    break
            if not done:
                # 第二優先：與相鄰 B 合併（含問句；過長時合併後拆句），
                # 務必讓佔比降回目標區間
                for i, ln in enumerate(out):
                    if ln.get("speaker") != "A":
                        continue
                    nxt = out[i + 1] if i + 1 < len(out) else None
                    prev = out[i - 1] if i > 0 else None
                    # 安全合併：合併後不得產生 3 連（否則與 streaks 互相抵消死循環）；
                    # 合併後若以 ？ 結尾，直接轉 。，避免又變回 B 問句。
                    if prev is not None and prev.get("speaker") == "B":
                        merged_text = prev["text"] + ln["text"]
                        if _is_question(merged_text):
                            merged_text = merged_text.rstrip()[:-1].rstrip() + "。"
                        pieces = split_text(merged_text) if len(merged_text) > MAX_LEN else [merged_text]
                        repl = [dict(prev, text=piece) for piece in pieces]
                        if _replacement_ok(out, i - 1, i + 1, repl):
                            out[i - 1:i + 1] = repl
                            changed = True
                            done = True
                            break
                    if nxt is not None and nxt.get("speaker") == "B":
                        merged_text = ln["text"] + nxt["text"]
                        if _is_question(merged_text):
                            merged_text = merged_text.rstrip()[:-1].rstrip() + "。"
                        pieces = split_text(merged_text) if len(merged_text) > MAX_LEN else [merged_text]
                        repl = [dict(nxt, text=piece) for piece in pieces]
                        if _replacement_ok(out, i, i + 2, repl):
                            out[i:i + 2] = repl
                            changed = True
                            done = True
                            break
        else:  # ratio < lo
            cands = []
            for i, ln in enumerate(out):
                if ln.get("speaker") != "B" or i == len(out) - 1:
                    continue
                if not _would_create_run(out, i, "A"):
                    cands.append((i, _is_question(ln.get("text", ""))))
            if cands:
                cands.sort(key=lambda x: (not x[1], x[0]))
                out[cands[0][0]]["speaker"] = "A"
                changed = True
                done = True
        if not done:
            break
        guard += 1
    return out, changed


def fix_b_questions(lines):
    """B 行出現「？」（中段或結尾）＝講解者唸問句：能安全改為 A 就改，否則把？改為。"""
    out = list(lines)
    changed = False
    n = len(out)
    for i in range(n - 1):
        ln = out[i]
        if ln.get("speaker") != "B":
            continue
        t = ln.get("text", "")
        if "？" not in t:
            continue
        prev_b = i > 0 and out[i - 1].get("speaker") == "B"
        next_b = i + 1 < n and out[i + 1].get("speaker") == "B"
        sandwiched = prev_b and next_b
        projected = _a_ratio(out) + 100.0 / n
        if (not sandwiched and projected <= A_MAX
                and not _would_create_run(out, i, "A")):
            ln["speaker"] = "A"
            changed = True
        else:
            ln["text"] = t.replace("？", "。")
            changed = True
    return out, changed


def _speaker_state_ok(lines):
    """檢查現況是否已滿足：無 3 連、A 佔比 30~38%。"""
    n = len(lines)
    if not n:
        return True
    prev = None
    run = 0
    for ln in lines:
        s = ln.get("speaker", "")
        run = run + 1 if s == prev else 1
        if run >= 3:
            return False
        prev = s
    r = _a_ratio(lines)
    return A_MIN <= r <= A_MAX


def _feasible(m, a):
    """m 個連續位置放 a 個 A（其餘 B），是否存在無 3 連的排法。"""
    if a < 0 or a > m:
        return False
    return a <= (m + 1) // 2 and (m - a) <= 2 * (a + 1)


def fix_speakers(lines):
    """最終保障：整份重排 speaker，保證無 3 連且 A 佔比 30~38%。

    問題行（結尾 ？）與原本就是 A 的行優先保留為 A；其餘行補 B。
    已合規的腳本直接原樣回傳（no-op）。
    """
    if _speaker_state_ok(lines):
        return lines, False
    n = len(lines)
    k = int(round(0.34 * n))
    lo = int(__import__("math").ceil(0.30 * n))
    hi = int(__import__("math").floor(0.38 * n))
    k = max(lo, min(hi, k))
    # 偏好：原本就是 A、結尾是問句、最後一行（咕咕嘎嘎收尾）
    prefer = [
        (ln.get("speaker") == "A")
        or _is_question(ln.get("text", ""))
        or (i == n - 1)
        for i, ln in enumerate(lines)
    ]
    pattern = []
    a_rem = k
    for i in range(n):
        m = n - i
        run_a = len(pattern) >= 2 and pattern[-1] == "A" and pattern[-2] == "A"
        run_b = len(pattern) >= 2 and pattern[-1] == "B" and pattern[-2] == "B"
        take_a = (a_rem > 0 and not run_a and _feasible(m - 1, a_rem - 1))
        b_rem = m - a_rem
        take_b = (b_rem > 0 and not run_b and _feasible(m - 1, a_rem))
        if prefer[i] and take_a:
            pattern.append("A"); a_rem -= 1
        elif take_b:
            pattern.append("B")
        elif take_a:
            pattern.append("A"); a_rem -= 1
        else:
            pattern.append("A" if a_rem > 0 else "B")
            if pattern[-1] == "A":
                a_rem -= 1
    out = [dict(ln) for ln in lines]
    for i, ln in enumerate(out):
        ln["speaker"] = pattern[i]
        if pattern[i] == "B" and "？" in ln.get("text", "") and i < n - 1:
            ln["text"] = ln["text"].replace("？", "。")
    return out, True


def fix_closer(lines):
    """末行若是「咕咕嘎嘎」收尾（風格上屬 Questioner），強制改為 A。

    A 佔比會因此 +1；若超過上限，則找一個非問句的 A 改成 B 補償。
    不含咕咕嘎嘎的腳本一律不動（保持對既有合規腳本的 no-op）。
    """
    out = list(lines)
    n = len(out)
    if n == 0 or "咕" not in out[-1].get("text", ""):
        return out, False
    if out[-1].get("speaker") == "A":
        return out, False
    if n >= 3 and out[-2].get("speaker") == "A" and out[-3].get("speaker") == "A":
        return out, False
    if _a_ratio(out) + 100.0 / n > A_MAX:
        for i in range(n - 2, -1, -1):
            ln = out[i]
            if ln.get("speaker") != "A" or _is_question(ln.get("text", "")):
                continue
            if _would_create_run(out, i, "B"):
                continue
            out[i]["speaker"] = "B"
            out[-1]["speaker"] = "A"
            return out, True
        return out, False
    out[-1]["speaker"] = "A"
    return out, True


_LEAD_PUNCT = "，。；！？、："


def fix_leading_punct(lines):
    """行首為標點（LLM 拆句殘留）→ 標點移到前一行行尾、行首剝離；空殘句整段併入前一行。"""
    out = list(lines)
    changed = False
    i = 1
    while i < len(out):
        text = out[i].get("text", "")
        lead = ""
        for ch in text:
            if ch in _LEAD_PUNCT:
                lead += ch
            else:
                break
        if not lead:
            i += 1
            continue
        rest = text[len(lead):].strip()
        if i == 0:
            out[i]["text"] = rest
            changed = True
            i += 1
            continue
        prev = out[i - 1]
        if not rest:
            prev["text"] = prev["text"].rstrip() + text
            del out[i]
            changed = True
            continue
        prev["text"] = prev["text"].rstrip() + lead
        out[i]["text"] = rest
        changed = True
        i += 1
    return out, changed


_EN_PAT = re.compile(r"[（(][A-Za-z][^）)]*[）)]")


def fix_text_english(lines):
    """text 移除英文括號（如 （Fork）／(API)）：英文只留給 terms，字幕／TTS 以純中文為主。"""
    out = list(lines)
    changed = False
    for ln in out:
        t = ln.get("text", "")
        t2 = _EN_PAT.sub("", t)
        t2 = re.sub(r" {2,}", " ", t2).strip()
        if t2 != t:
            ln["text"] = t2
            changed = True
    return out, changed


# 單字前綴（和／像／怕）也是常見合法詞首（和平、像素），只在餘下為拉丁／數字時
# 才剝離（如 "和AI" "像API"）；多字前綴（透過／就是／他們會）可安全剝離。
_TERM_ASCII_REST = re.compile(r"^[A-Za-z0-9]")
TERM_STOP_PREFIXES = ("透過", "像", "怕", "就是", "他們會", "和")


def fix_terms_prefix(lines):
    """terms.cn 帶 stopword 前綴（透過／像／怕／就是／他們會／和）→ 剝離前綴，避免 validate 警告。"""
    out = list(lines)
    changed = False
    for ln in out:
        for t in ln.get("terms") or []:
            cn = str(t.get("cn", ""))
            for sp in TERM_STOP_PREFIXES:
                if not cn.startswith(sp) or len(cn) <= len(sp) + 1:
                    continue
                rest = cn[len(sp):].strip(" 　、，。")
                if not rest:
                    continue
                if len(sp) == 1 and not _TERM_ASCII_REST.match(rest):
                    continue  # 單字前綴：餘下為中文＝合法詞首（和平、像素），不動
                t["cn"] = rest
                changed = True
                break
    return out, changed


def apply_fixes(script):
    """依序套用：拆句 → 連續修正 → A 佔比 → B 問句。回傳 (script, changed)。"""
    lines = script.get("lines") or []
    changed = False
    lines, c = split_long_lines(lines)
    changed = changed or c
    lines, c = fix_leading_punct(lines)
    changed = changed or c
    lines, c = fix_text_english(lines)
    changed = changed or c
    lines, c = fix_short_lines(lines)
    changed = changed or c
    lines, c = fix_speaker_streaks(lines)
    changed = changed or c
    lines, c = fix_a_ratio(lines)
    changed = changed or c
    lines, c = fix_b_questions(lines)
    changed = changed or c
    lines, c = fix_speakers(lines)
    changed = changed or c
    lines, c = fix_closer(lines)
    changed = changed or c
    lines, c = fix_terms_prefix(lines)
    changed = changed or c
    out = dict(script)
    out["lines"] = lines
    return out, changed


def _validate(lines):
    from learntok.tools import validate_script as vs
    script = {
        "id": "fix_test",
        "title": "fix test",
        "resolution": "720x1280",
        "characters": {
            "A": {"name": "企鵝燈", "role": "questioner", "color": "#FFD54F"},
            "B": {"name": "派大星", "role": "explainer", "color": "#4FC3F7"},
        },
        "lines": lines,
    }
    os.makedirs(BUILD_DIR, exist_ok=True)
    p = os.path.join(BUILD_DIR, ".script_fix_selftest.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(script, fh, ensure_ascii=False, indent=2)
    return vs.validate(p)


def _self_test():
    fails = []

    def check(name, lines, ignore=(), expect_changed=None):
        fixed, changed = apply_fixes({"lines": lines})
        errs, warns = _validate(fixed["lines"])
        leftover = [w for w in warns if not any(k in w for k in ignore)]
        ok = (not errs) and (not leftover)
        if expect_changed is not None and changed != expect_changed:
            ok = False
        print("  [%s] %-18s errors=%d warnings=%d(ignore=%d) changed=%s"
              % ("OK" if ok else "FAIL", name, len(errs), len(leftover), len(ignore), changed))
        if not ok:
            fails.append(name)
            for e in errs[:4]:
                print("      E: %s" % e)
            for w in leftover[:4]:
                print("      W: %s" % w)

    def check_converge(name, lines, max_passes=6):
        script = {"lines": lines}
        used = 0
        for used in range(1, max_passes + 1):
            fixed, changed = apply_fixes(script)
            script = fixed
            if not changed:
                break
        errs, warns = _validate(script["lines"])
        ok = (not errs) and (not warns)
        print("  [%s] %-18s errors=%d warnings=%d passes=%d"
              % ("OK" if ok else "FAIL", name, len(errs), len(warns), used))
        if not ok:
            fails.append(name)
            for e in errs[:4]:
                print("      E: %s" % e)
            for w in warns[:4]:
                print("      W: %s" % w)

    print("script_fix self-test")
    # 1. 超長句拆句（目標：拆後無行長/短行警告；A 佔比警告忽略）
    check("split-long-line", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就是設計優化輸入"},
        {"speaker": "A", "text": "那跟平常講話一樣嗎，AI 真的有聽懂我說的每一個字嗎"},
        {"speaker": "B", "text": "其實就是好好下指令"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "最好再給些實際範例"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
    ], ignore=("A 佔比",), expect_changed=True)

    # 2. B 連 3+ 行（目標：無連續錯誤與短行；其餘警告忽略）
    check("b-streak", [
        {"speaker": "B", "text": "第一句先講解定義"},
        {"speaker": "B", "text": "第二句再舉個例子"},
        {"speaker": "B", "text": "第三句做個小結尾"},
        {"speaker": "B", "text": "第四句做個總結吧"},
        {"speaker": "A", "text": "那接下來該怎麼辦呢"},
        {"speaker": "B", "text": "大概就是這樣子了"},
        {"speaker": "A", "text": "聽起來好像挺合理的"},
        {"speaker": "A", "text": "還有什麼要注意的嗎"},
        {"speaker": "B", "text": "下次再試試看好了"},
        {"speaker": "A", "text": "這下我總算聽懂了"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "B", "text": "練習久了就會越來越順"},
        {"speaker": "B", "text": "要記得反覆多練習"},
    ], ignore=("A 佔比",), expect_changed=True)

    # 3. A 佔比過高 43.75% -> 30~38%（目標：無任何錯誤與警告）
    check("a-ratio-high", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就是設計優化輸入"},
        {"speaker": "A", "text": "那跟平常講話一樣嗎"},
        {"speaker": "A", "text": "感覺好像很複雜啊"},
        {"speaker": "B", "text": "其實就是好好下指令"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "最好再給些實際範例"},
        {"speaker": "A", "text": "範例大概要給幾個呢"},
        {"speaker": "B", "text": "兩三個就很夠用了"},
        {"speaker": "A", "text": "還有沒有別的秘訣"},
        {"speaker": "B", "text": "指令和內容要分開"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "A", "text": "聽起來好像挺合理的"},
        {"speaker": "B", "text": "下次再試試看好了"},
        {"speaker": "B", "text": "練習久了就會越來越順"},
    ], ignore=(), expect_changed=True)

    # 4. B 中段問句（目標：無 B 問句警告；其餘警告忽略）
    check("b-question", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "你知道該怎麼問嗎？"},
        {"speaker": "B", "text": "其實就是好好說清楚"},
        {"speaker": "A", "text": "我其實不太確定耶"},
        {"speaker": "B", "text": "多試幾次就會熟了"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
    ], ignore=("A 佔比",), expect_changed=True)

    # 5. 已合規腳本：不應被改動（regression）
    compliant = [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就是設計優化輸入"},
        {"speaker": "B", "text": "講解得越清楚越好"},
        {"speaker": "A", "text": "那跟平常講話一樣嗎"},
        {"speaker": "B", "text": "其實就是好好下指令"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "最好再給些實際範例"},
        {"speaker": "B", "text": "兩三個就很夠用了"},
        {"speaker": "A", "text": "範例大概要給幾個呢"},
        {"speaker": "B", "text": "順序其實也很重要"},
        {"speaker": "A", "text": "這下我總算聽懂了"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "B", "text": "有問題隨時再問我"},
    ]
    fixed, changed = apply_fixes({"lines": compliant})
    same = fixed["lines"] == compliant
    errs, warns = _validate(fixed["lines"])
    ok = (not changed) and same and (not errs) and (not warns)
    print("  [%s] %-18s changed=%s identical=%s errors=%d warnings=%d"
          % ("OK" if ok else "FAIL", "regression", changed, same, len(errs), len(warns)))
    if not ok:
        fails.append("regression")

    # 6. 長句以「，」結尾（防止 split_text 回補死循環）
    check("trailing-boundary", [
        {"speaker": "B", "text": "提示工程就是設計優化輸入"},
        {"speaker": "A", "text": "為什麼要學提示工程呢？"},
        {"speaker": "B", "text": "因為提示寫得好，AI 給的答案品質就穩定很多，所以人人都該學。"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
    ], ignore=("A 佔比",), expect_changed=True)

    # 7. 短行夾在長句中間：合併後拆句，反覆到 0/0
    check_converge("short-merge-split", [
        {"speaker": "B", "text": "提示工程就是設計優化輸入"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "設計並優化輸入，引導 AI 產出穩定好結果。"},
        {"speaker": "A", "text": "那不是瞎猜嗎。"},
        {"speaker": "B", "text": "是統計上的猜，它讀過超多文本，知道詞語常一起出現。"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
    ])

    # 8. 全 A 皆問句且被 B 夾住（B,B,A 交替）→ 增量修復會振盪，需 fix_speakers 收斂
    check_converge("speaker-oscillation", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢？"},
        {"speaker": "B", "text": "就是設計優化輸入"},
        {"speaker": "B", "text": "讓 AI 穩定產出好結果"},
        {"speaker": "A", "text": "那它真的懂我在問什麼嗎？"},
        {"speaker": "B", "text": "其實它只看到一串 token"},
        {"speaker": "A", "text": "那不是全在瞎猜嗎？"},
        {"speaker": "B", "text": "是統計上的猜測"},
        {"speaker": "B", "text": "它讀過超多文本"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎？"},
        {"speaker": "B", "text": "多練習幾次就熟了"},
        {"speaker": "A", "text": "那不同模型差很多嗎？"},
        {"speaker": "B", "text": "能力有差異"},
        {"speaker": "B", "text": "成本也不一樣"},
        {"speaker": "A", "text": "還有沒有別的秘訣？"},
        {"speaker": "B", "text": "指令和內容要分開"},
    ])

    # 9. 末行 B 說咕咕嘎嘎 → fix_closer 改為 A，且維持 0/0
    check("closer-to-a", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就是設計優化輸入"},
        {"speaker": "A", "text": "那跟平常講話一樣嗎"},
        {"speaker": "B", "text": "其實就是好好下指令"},
        {"speaker": "B", "text": "兩三個範例就很夠用了"},
        {"speaker": "A", "text": "範例大概要給幾個呢"},
        {"speaker": "B", "text": "順序其實也很重要"},
        {"speaker": "B", "text": "順序影響也很大"},
        {"speaker": "A", "text": "這下我總算聽懂了"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "B", "text": "還有內容要繼續說明"},
        {"speaker": "A", "text": "那還有什麼要注意的"},
        {"speaker": "B", "text": "記得要有實際範例"},
        {"speaker": "B", "text": "練習久了自然會熟練"},
        {"speaker": "B", "text": "有問題隨時再問我，咕咕嘎嘎！"},
    ], ignore=(), expect_changed=True)

    # 10. 被 B 夾住的 B 問句 → 只能 ？→。，不能轉 A
    check("b-question-sandwiched", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "你知道該怎麼問嗎？"},
        {"speaker": "B", "text": "其實就是好好說清楚"},
        {"speaker": "A", "text": "我其實不太確定耶"},
        {"speaker": "B", "text": "多試幾次就會熟了"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
    ], ignore=("A 佔比",), expect_changed=True)

    # 11. terms cn 帶 stopword 前綴 → 剝離前綴（0 警告）
    check("terms-prefix", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就是設計優化輸入", "terms": [{"cn": "就是提示工程", "en": "Prompt Engineering"}]},
        {"speaker": "B", "text": "像AI 這樣最好", "terms": [{"cn": "像AI", "en": "AI"}]},
        {"speaker": "A", "text": "那跟平常講話一樣嗎"},
        {"speaker": "B", "text": "透過股東大會投票", "terms": [{"cn": "透過股東大會", "en": "Shareholder Meeting"}]},
        {"speaker": "B", "text": "他們會幻想出不存在的內容", "terms": [{"cn": "他們會幻想出不存在的內容", "en": "Hallucination"}]},
        {"speaker": "B", "text": "和平協議保障權益", "terms": [{"cn": "和平協議", "en": "Peace Agreement"}]},
        {"speaker": "B", "text": "像素密度影響畫質", "terms": [{"cn": "像素密度", "en": "Pixel Density"}]},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "A", "text": "聽起來好像挺合理的"},
        {"speaker": "B", "text": "下次再試試看好了"},
    ], ignore=("A 佔比",), expect_changed=True)
    # 單字前綴誤傷防護：合法詞首（和平、像素）不得被剝離
    guard, _gc = fix_terms_prefix([
        {"speaker": "B", "text": "x", "terms": [{"cn": "和平協議", "en": "P"}, {"cn": "像素密度", "en": "P"}]}])
    kept = [t["cn"] for t in guard[0]["terms"]]
    if kept != ["和平協議", "像素密度"]:
        fails.append("terms-prefix-guard")
        print("  [FAIL] terms-prefix-guard kept=%s" % kept)
    else:
        print("  [OK]   terms-prefix-guard 合法詞首保留: %s" % "、".join(kept))

    # 12. 行首標點（拆句殘留）→ 標點移前行尾、短句併回，收斂到 0/0
    check_converge("leading-punct", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "就像你去吃麵，說『隨便』跟說『少油多蔥』"},
        {"speaker": "A", "text": "，結果會一樣嗎。"},
        {"speaker": "B", "text": "當然不一樣，所以提示就是 AI 的指令。"},
        {"speaker": "B", "text": "對，提示是程式介面，指令品質決定回應品質。"},
        {"speaker": "A", "text": "那提示詞會影響品質嗎"},
        {"speaker": "B", "text": "影響可大了，差一個字就差很多。"},
        {"speaker": "B", "text": "記得指令品質決定回應品質。"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了。"},
        {"speaker": "B", "text": "練習久了就會越來越順。"},
        {"speaker": "A", "text": "那還有什麼要注意的嗎"},
        {"speaker": "B", "text": "記得一定要多練習。"},
    ])

    # 13. B 中段出現「？」→ 能安全改 A 就改（角色歸位），0/0
    check_converge("b-question-mid", [
        {"speaker": "B", "text": "提示工程先講定義"},
        {"speaker": "A", "text": "什麼是提示工程呢"},
        {"speaker": "B", "text": "環境變數？聽起來好專業。"},
        {"speaker": "B", "text": "其實就是好好下指令"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了"},
        {"speaker": "A", "text": "聽起來好像挺合理的"},
        {"speaker": "B", "text": "下次再試試看好了"},
        {"speaker": "B", "text": "記得一定要多練習"},
        {"speaker": "B", "text": "練習久了就會越來越順"},
        {"speaker": "A", "text": "這下我總算聽懂了"},
        {"speaker": "B", "text": "有問題隨時再問我"},
        {"speaker": "B", "text": "順序其實也很重要"},
        {"speaker": "B", "text": "兩三個就很夠用了"},
        {"speaker": "A", "text": "範例大概要給幾個呢"},
        {"speaker": "B", "text": "記得指令品質決定回應品質。"},
    ])

    # 14. text 含英文括號 → 剝離，0/0
    check("strip-english", [
        {"speaker": "B", "text": "先分叉（Fork）儲存庫到你的帳號。"},
        {"speaker": "A", "text": "分叉？是把程式碼切兩半嗎？"},
        {"speaker": "B", "text": "不是，是複製一份到你的帳號。"},
        {"speaker": "A", "text": "那複製完，然後呢？"},
        {"speaker": "B", "text": "建立雲端開發環境。"},
        {"speaker": "B", "text": "開啟瀏覽器就能寫。"},
        {"speaker": "A", "text": "聽起來很方便。"},
        {"speaker": "B", "text": "金鑰要放環境變數。"},
        {"speaker": "B", "text": "程式執行時自動讀取。"},
        {"speaker": "A", "text": "這樣做真的就夠了嗎"},
        {"speaker": "B", "text": "慢慢練習就會上手了。"},
        {"speaker": "A", "text": "聽起來好像挺合理的。"},
        {"speaker": "B", "text": "下次再試試看好了。"},
    ], ignore=("A 佔比",), expect_changed=True)

    print("RESULT:", "PASS" if not fails else "FAIL(%s)" % ", ".join(fails))
    return 0 if not fails else 1


def _fix_file(path, out_path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        script = json.load(fh)
    fixed, changed = apply_fixes(script)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fixed, fh, ensure_ascii=False, indent=2)
    print("script_fix: %s %s（%s 行）" % ("已修正" if changed else "無需變更", out_path, len(fixed["lines"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description="script_fix — deterministic post-processing")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fix", default=None, help="修正腳本 JSON（原地寫回）")
    ap.add_argument("--out", default=None, help="--fix 的輸出路徑（預設原地）")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(_self_test())
    if args.fix:
        sys.exit(_fix_file(args.fix, args.out or args.fix))
    ap.print_help()


if __name__ == "__main__":
    main()