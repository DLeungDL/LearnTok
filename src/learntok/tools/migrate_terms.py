"""
migrate_terms.py — 把腳本 JSON 的行內英文括號格式遷移成結構化 terms 欄位。

舊格式（inline parens）:
  {"text": "股東可以透過股東大會（Shareholder Meetings）投票"}

新格式（structured terms）:
  {"text": "股東可以透過股東大會投票",
   "terms": [{"cn": "股東大會", "en": "Shareholder Meetings"}]}

text 變純中文（TTS 直接唸，strip_english 為 no-op，cache key 不變）。
terms 的 cn 用 _extract_cn_term 反向最大匹配剝離前綴作為初始值，
人類可手動覆審少數偏冗長者。

用法:
  python migrate_terms.py --script pipeline/examples/xxx.json [--in-place]
"""
import argparse
import io
import json
import os
import re
import sys

from learntok.compose import _extract_cn_term  # noqa: E402

PAT = re.compile(r"([\u4e00-\u9fff]+)[（(]([A-Za-z][^）)]*)[）)]")
PAREN_RE = re.compile(r"[（(][A-Za-z][^）)]*[）)]")


def migrate_line(ln):
    """Return (new_text, terms, changed)."""
    text = ln.get("text", "")
    if "terms" in ln and ln["terms"]:
        return text, ln["terms"], False  # already migrated
    pairs = PAT.findall(text)
    if not pairs:
        return text, [], False
    terms = [{"cn": _extract_cn_term(cn), "en": en} for cn, en in pairs]
    new_text = PAREN_RE.sub("", text)
    new_text = re.sub(r" {2,}", " ", new_text).strip()
    return new_text, terms, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--in-place", action="store_true", help="寫回原檔（否則只預覽）")
    args = ap.parse_args()

    with io.open(args.script, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    changed = 0
    for ln in data.get("lines", []):
        new_text, terms, did = migrate_line(ln)
        if did:
            if args.in_place:
                ln["text"] = new_text
                ln["terms"] = terms
            else:
                print("--- line %s ---" % ln.get("id", "?"))
                print("  OLD: %s" % ln.get("text", ""))
                print("  NEW text: %s" % new_text)
                print("  terms: %s" % json.dumps(terms, ensure_ascii=False))
            changed += 1

    if args.in_place and changed:
        with io.open(args.script, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("wrote %s: migrated %d lines" % (args.script, changed))
    elif not args.in_place:
        print("\n%d lines would be migrated (use --in-place to write)" % changed)
    else:
        print("no inline-paren lines found; nothing to do")


if __name__ == "__main__":
    main()