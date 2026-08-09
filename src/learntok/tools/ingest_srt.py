#!/usr/bin/env python3
"""Convert an SRT subtitle file into a pipeline script JSON.

Note: SRT has no speaker labels. The built-in heuristic (questions/short
interjections -> A, otherwise -> B) is a DEMO shortcut only; production
scripts should come from the LLM with proper speaker tags.
"""
import argparse
import json
import re

QUESTION_ENDS = ("吗", "呢", "吧", "？", "?", "嘛", "啊")

TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def to_sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def guess_speaker(text):
    t = text.strip()
    if t.endswith(QUESTION_ENDS):
        return "A"
    if len(t) <= 6 and not t.endswith("。"):
        return "A"
    return "B"


def parse(path, max_duration=0.0):
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().replace("\r\n", "\n")
    lines = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        rows = [r.strip() for r in block.split("\n") if r.strip()]
        if not rows:
            continue
        ti = 1 if rows[0].isdigit() and len(rows) > 1 else 0
        if ti >= len(rows):
            continue
        m = TS.search(rows[ti])
        if not m:
            continue
        text = "".join(rows[ti + 1:]).strip()
        if not text:
            continue
        start = to_sec(*m.groups()[:4])
        end = to_sec(*m.groups()[4:])
        if max_duration and start >= max_duration:
            break
        if max_duration:
            end = min(end, max_duration)
        lines.append({
            "speaker": guess_speaker(text),
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
        })
    return lines


def main():
    ap = argparse.ArgumentParser(description="SRT -> pipeline script JSON")
    ap.add_argument("--srt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--id", default="sample")
    ap.add_argument("--title", default="")
    ap.add_argument("--max-duration", type=float, default=0.0)
    args = ap.parse_args()

    lines = parse(args.srt, args.max_duration)
    script = {
        "id": args.id,
        "title": args.title,
        "resolution": "720x1280",
        "characters": {
            "A": {"name": "企鵝燈", "role": "questioner", "color": "#FFD54F"},
            "B": {"name": "熊大", "role": "explainer", "color": "#81C784"},
        },
        "lines": lines,
        "diagram_cards": [],
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(script, fh, ensure_ascii=False, indent=2)
    total = max(ln["end"] for ln in lines) if lines else 0
    print("wrote %s: %d lines, %.1fs" % (args.out, len(lines), total))


if __name__ == "__main__":
    main()