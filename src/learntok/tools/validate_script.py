"""
validate_script.py — 腳本生成後的自動品質檢查。

檢查項目：
1. 連續同行者：不得超過 2 行同一 speaker（防止自言自語）
2. 問句歸屬：以「？」結尾的行通常是 A（Questioner）
3. A 佔比：目標 30-38%（超出 25-40 為錯誤）
4. 行長：8-25 字
5. 咕咕嘎嘎：只出現在最後一行
6. 禁用詞彙
7. terms 格式：cn 不含前綴動詞
8. 角色名稱與 characters.json 一致

用法：
  python -m learntok.tools.validate_script --script pipeline/examples/xxx.json
"""
import argparse
import io
import json
import os
import re
import sys

from learntok import config

BANNED_WORDS = [
    "\u767d\u5ad2",  # 白嫖
    "\u5c44",        # 屄
    "\u5e79",        # 幹
]
GUGU = "\u5495\u5495\u560e\u560e"  # 咕咕嘎嘎


def load_chars():
    cjson = os.path.join(config.assets_root(), "characters.json")
    if os.path.isfile(cjson):
        with io.open(cjson, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def validate(path, require_rag_sources=False, rag_collection="leantok_kb", rag_db=None):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    errors = []
    warnings = []
    lines = data.get("lines", [])
    n = len(lines)
    if n == 0:
        return ["no lines"], []

    # 1. Consecutive same speaker (max 2)
    prev = None
    streak = 0
    for i, ln in enumerate(lines):
        sp = ln.get("speaker", "")
        if sp == prev:
            streak += 1
            if streak >= 2:
                errors.append("L%d: %s 連續 %d 行（自言自語風險）: %s" % (i+1, sp, streak+1, ln.get("text","")[:30]))
        else:
            streak = 0
        prev = sp

    # 2. Question lines (ending with ？) should typically be A
    for i, ln in enumerate(lines):
        t = ln.get("text", "").rstrip()
        if t.endswith("\uff1f") and ln.get("speaker") == "B" and i < n - 1:
            # B asking a question mid-script is suspicious (except rhetorical teaching questions)
            warnings.append("L%d [B]: 問句可能是 A 的台詞? %s" % (i+1, t[:35]))

    # 3. A ratio
    a_count = sum(1 for ln in lines if ln.get("speaker") == "A")
    ratio = a_count / n * 100 if n else 0
    if ratio > 40:
        errors.append("A 佔比 %.0f%%（超過上限 40%%）" % ratio)
    elif ratio > 38:
        warnings.append("A 佔比 %.0f%%（偏高，目標 30-38%%）" % ratio)
    elif ratio < 25:
        errors.append("A 佔比 %.0f%%（低於下限 25%%）" % ratio)
    elif ratio < 30:
        warnings.append("A 佔比 %.0f%%（偏低，目標 30-38%%）" % ratio)

    # 4. Line length
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if len(t) > 25:
            warnings.append("L%d: %d 字（超過 25）: %s" % (i+1, len(t), t[:30]))
        if len(t) < 8:
            warnings.append("L%d: %d 字（少於 8）: %s" % (i+1, len(t), t))

    # 4b. ASS 控制字元 — 會注入字幕覆寫標籤（override tag）
    ASS_CTRL_CHARS = ("{", "}", "\\", "\r", "\t")
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if any(c in t for c in ASS_CTRL_CHARS):
            errors.append("L%d: 台詞含 ASS 控制字元（{ } \\ CR tab，字幕注入風險）: %s" % (i+1, t[:30]))
        for t_obj in ln.get("terms", []) or []:
            blob = "%s%s" % (t_obj.get("cn", ""), t_obj.get("en", ""))
            if any(c in blob for c in ASS_CTRL_CHARS):
                errors.append("L%d: terms 含 ASS 控制字元（{ } \\ CR tab）: %s" % (i+1, blob[:30]))

    # 5. 咕咕嘎嘎 only in last line
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if "\u5495" in t and i < n - 1:
            errors.append("L%d: 咕咕嘎嘎出現在中間行（只允許最後一行）: %s" % (i+1, t[:30]))

    # 6. Banned words
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        for bw in BANNED_WORDS:
            if bw in t:
                errors.append("L%d: 禁用詞「%s」: %s" % (i+1, bw, t[:30]))

    # 7. terms cn check — no leading stopwords
    STOP_PREFIXES = ["\u900f\u904e", "\u50cf", "\u6015", "\u5c31\u662f", "\u4ed6\u5011\u6703", "\u548c"]
    # 與 script_fix.py 同步：單字前綴（和／像／怕）只在餘下為拉丁／數字時警告（如和AI）
    for i, ln in enumerate(lines):
        for t_obj in ln.get("terms", []) or []:
            cn = t_obj.get("cn", "")
            for sp in STOP_PREFIXES:
                if not (cn.startswith(sp) and len(cn) > len(sp) + 1):
                    continue
                rest = cn[len(sp):].strip(" 　、，。")
                if len(sp) == 1 and not re.match(r"^[A-Za-z0-9]", rest):
                    continue  # 單字前綴：餘下為中文＝合法詞首（和平、像素），不警告
                warnings.append("L%d: terms cn「%s」可能有前綴「%s」" % (i+1, cn, sp))

    # 8. Character name check
    chars_cfg = load_chars()
    for key, ch in data.get("characters", {}).items():
        name = ch.get("name", "")
        if chars_cfg and name not in chars_cfg:
            errors.append("characters.%s name「%s」不在 characters.json 中" % (key, name))

    # 8b. 提示詞注入標記 — 台詞不得引用系統提示／角色設定文字
    INJECT_MARKERS = (
        "system prompt", "system_prompt", "ignore all previous",
        "ignore previous instructions", "忽略所有之前的指令", "忽略之前所有指令",
    )
    for i, ln in enumerate(lines):
        low = ln.get("text", "").lower()
        if any(m in low for m in INJECT_MARKERS):
            errors.append("L%d: 台詞疑似引用系統提示（注入風險）: %s" % (i+1, ln.get("text", "")[:40]))

    # 9. RAG source check (--rag-sources)
    if require_rag_sources:
        for i, ln in enumerate(lines):
            for t_obj in ln.get("terms", []) or []:
                src = (t_obj.get("source") or "").strip()
                if not src:
                    errors.append("L%d: terms「%s」缺少 source 出處（RAG 檢核）" % (i + 1, t_obj.get("cn", "")))
        if rag_db:
            try:
                from learntok.tools import rag_common as _rag
                _client = _rag.get_client(rag_db)
                _col = _client.get_collection(rag_collection)
                for i, ln in enumerate(lines):
                    for t_obj in ln.get("terms", []) or []:
                        src = (t_obj.get("source") or "").strip()
                        if src:
                            src_norm = re.sub(r":\d+$", "", src.replace("\\", "/"))
                            got = _col.get(where={"source": src_norm}, limit=1)
                            if not (got and got.get("ids")):
                                got = _col.get(where={"source": src_norm.replace("/", "\\")}, limit=1)
                            if not (got and got.get("ids")):
                                errors.append("L%d: terms「%s」的 source「%s」不在知識庫中" % (i + 1, t_obj.get("cn", ""), src))
            except Exception as exc:
                errors.append("RAG 檢核無法連線知識庫: %s" % exc)

    return errors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--rag-sources", action="store_true",
                    help="require terms[].source and verify against the RAG knowledge base")
    ap.add_argument("--rag-collection", default="leantok_kb")
    ap.add_argument("--rag-db", default=None, help="ChromaDB path (default assets/rag/chroma)")
    args = ap.parse_args()

    rag_db = args.rag_db
    if rag_db is None:
        rag_db = os.path.join(config.workspace_root(), "assets", "rag", "chroma")
    errors, warnings = validate(args.script,
                                require_rag_sources=args.rag_sources,
                                rag_collection=args.rag_collection,
                                rag_db=rag_db)

    if warnings:
        print("\u26a0 \u8b66\u544a (%d):" % len(warnings))
        for w in warnings:
            print("  %s" % w)
    if errors:
        print("\u2716 \u932f\u8aa4 (%d):" % len(errors))
        for e in errors:
            print("  %s" % e)
        print("\n\u2716 %d \u500b\u932f\u8aa4\uff0c\u8acb\u4fee\u6b63\u5f8c\u518d\u8dd1 TTS" % len(errors))
        sys.exit(1)
    elif not warnings:
        print("\u2714 \u5168\u90e8\u901a\u904e\uff0c\u7121\u554f\u984c")
    else:
        print("\u2714 \u7121\u932f\u8aa4\uff0c\u4f46\u6709 %d \u500b\u8b66\u544a\u5efa\u8b70\u6aa2\u67e5" % len(warnings))


if __name__ == "__main__":
    main()
