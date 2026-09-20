"""
validate_script.py -- quality gate after script generation.
"""
import argparse
import io
import json
import os
import re
import sys

from learntok import config

BANNED_WORDS = [
    "\u767d\u5ad2",
    "\u5c44",
    "\u5e79",
]
GUGU = "\u5495\u5495\u560e\u560e"
QUESTIONER_VOICE = re.compile(
    r"(?:" + "|".join([
        "\u86e4",
        "\u554a\uff1f",
        "\u5443\u2026",
        r"\u5443\.\.\.",
        "\u6211\u4e0d\u77e5\u9053",
        "\u771f\u7684\u5047\u7684",
        "\u600e\u9ebc\u53ef\u80fd",
        "\u807d\u8d77\u4f86\u597d\u8907\u96dc",
        r"^\u7b49\u7b49[,\uff0c]?",
        r"^\u7b49\u4e00\u4e0b",
    ]) + ")"
)
TEACHING_VOICE = re.compile(
    r"(?:" + "|".join([
        "\u5176\u5be6",
        "\u7c21\u55ae\u8aaa",
        "\u610f\u601d\u662f",
        "\u63db\u53e5\u8a71\u8aaa",
        "\u7814\u7a76\u986f\u793a",
        "\u8ad6\u6587",
        "\u6a21\u578b\u5047\u8a2d",
    ]) + ")"
)


def load_chars():
    cjson = os.path.join(config.assets_root(), "characters.json")
    if os.path.isfile(cjson):
        with io.open(cjson, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def _is_question(text):
    return (text or "").rstrip().endswith(("\uff1f", "?"))


def resolve_bible(bible, script_id=None, episode_id=None):
    """Map a series bible (or already-resolved object) to {episode: ...}.

    `validate()` reads recap_from_prev from bible["episode"] or the object
    itself. A standard series bible has `episodes`, so the CLI must pick one.
    """
    if not bible:
        return None
    if isinstance(bible.get("episode"), dict):
        return {"episode": bible["episode"]}
    eps = bible.get("episodes")
    if isinstance(eps, list):
        want = episode_id or script_id
        if not want:
            return None
        for ep in eps:
            if ep.get("id") == want:
                return {"episode": ep}
        return None
    if bible.get("recap_from_prev") is not None or bible.get("must_cover") or bible.get("id"):
        return {"episode": bible}
    return None


def validate(path, require_rag_sources=False, rag_collection="leantok_kb", rag_db=None,
             min_lines=0, bible=None, strict_voice=False):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    errors = []
    warnings = []
    lines = data.get("lines", [])
    n = len(lines)
    if n == 0:
        return ["no lines"], []

    prev = None
    streak = 0
    for i, ln in enumerate(lines):
        sp = ln.get("speaker", "")
        if sp == prev:
            streak += 1
            if streak >= 2:
                errors.append("L%d: %s consecutive %d lines: %s" % (i+1, sp, streak+1, ln.get("text","")[:30]))
        else:
            streak = 0
        prev = sp

    for i, ln in enumerate(lines):
        t = ln.get("text", "").rstrip()
        if t.endswith("\uff1f") and ln.get("speaker") == "B" and i < n - 1:
            warnings.append("L%d [B]: question may belong to A: %s" % (i+1, t[:35]))

    first = lines[0]
    first_t = first.get("text", "")
    if first.get("speaker") == "B" and (_is_question(first_t) or QUESTIONER_VOICE.search(first_t)):
        msg = "L1 [B]: opener is questioner voice, should be A: %s" % first_t[:35]
        (errors if strict_voice else warnings).append(msg)
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        sp = ln.get("speaker", "")
        if sp == "B" and QUESTIONER_VOICE.search(t):
            msg = "L%d [B]: questioner voice should be A: %s" % (i + 1, t[:35])
            if i == 0 or strict_voice:
                errors.append(msg)
            else:
                warnings.append(msg)
        if sp == "A" and not _is_question(t) and TEACHING_VOICE.search(t) and len(t) >= 18:
            warnings.append("L%d [A]: teaching voice, maybe B: %s" % (i + 1, t[:35]))

    a_count = sum(1 for ln in lines if ln.get("speaker") == "A")
    ratio = a_count / n * 100 if n else 0
    if ratio > 40:
        errors.append("A ratio %.0f%% (above 40%%)" % ratio)
    elif ratio > 38:
        warnings.append("A ratio %.0f%% (high, target 30-38%%)" % ratio)
    elif ratio < 25:
        errors.append("A ratio %.0f%% (below 25%%)" % ratio)
    elif ratio < 30:
        warnings.append("A ratio %.0f%% (low, target 30-38%%)" % ratio)

    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if len(t) > 25:
            warnings.append("L%d: %d chars (>25): %s" % (i+1, len(t), t[:30]))
        if len(t) < 8:
            warnings.append("L%d: %d chars (<8): %s" % (i+1, len(t), t))

    ASS_CTRL_CHARS = ("{", "}", "\\", "\r", "\t")
    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if any(c in t for c in ASS_CTRL_CHARS):
            errors.append("L%d: ASS control chars in text: %s" % (i+1, t[:30]))
        for t_obj in ln.get("terms", []) or []:
            blob = "%s%s" % (t_obj.get("cn", ""), t_obj.get("en", ""))
            if any(c in blob for c in ASS_CTRL_CHARS):
                errors.append("L%d: ASS control chars in terms: %s" % (i+1, blob[:30]))

    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        if "\u5495" in t and i < n - 1:
            errors.append("L%d: gugu appears before last line: %s" % (i+1, t[:30]))
    last_t = lines[-1].get("text", "")
    if "\u5495" in last_t and lines[-1].get("speaker") != "A":
        errors.append("L%d: gugu closer must be A, got %s" % (n, lines[-1].get("speaker")))

    if min_lines and n < min_lines:
        errors.append("line count %d below min %d" % (n, min_lines))

    if bible:
        ep = bible.get("episode") or bible
        recap = (ep.get("recap_from_prev") or "").strip()
        if recap and n >= 6:
            window = "".join(ln.get("text", "") for ln in lines[:8])
            needles = [w for w in re.split(r"[,;\s\u3001\u3002\uff0c\uff1b]+", recap) if len(w) >= 4][:3]
            if needles and not any(w in window for w in needles):
                warnings.append("missing series recap (expected: %s)" % "/".join(needles))

    for i, ln in enumerate(lines):
        t = ln.get("text", "")
        for bw in BANNED_WORDS:
            if bw in t:
                errors.append("L%d: banned word: %s" % (i+1, t[:30]))

    STOP_PREFIXES = ["\u900f\u904e", "\u50cf", "\u6015", "\u5c31\u662f", "\u4ed6\u5011\u6703", "\u548c"]
    for i, ln in enumerate(lines):
        for t_obj in ln.get("terms", []) or []:
            cn = t_obj.get("cn", "")
            for sp in STOP_PREFIXES:
                if not (cn.startswith(sp) and len(cn) > len(sp) + 1):
                    continue
                rest = cn[len(sp):].strip(" \u3000\u3001\uff0c\u3002")
                if len(sp) == 1 and not re.match(r"^[A-Za-z0-9]", rest):
                    continue
                warnings.append("L%d: terms cn may have prefix %s: %s" % (i+1, sp, cn))

    chars_cfg = load_chars()
    for key, ch in data.get("characters", {}).items():
        name = ch.get("name", "")
        if chars_cfg and name not in chars_cfg:
            errors.append("characters.%s name %s not in characters.json" % (key, name))

    INJECT_MARKERS = (
        "system prompt", "system_prompt", "ignore all previous",
        "ignore previous instructions",
        "\u5ffd\u7565\u6240\u6709\u4e4b\u524d\u7684\u6307\u4ee4",
        "\u5ffd\u7565\u4e4b\u524d\u6240\u6709\u6307\u4ee4",
    )
    for i, ln in enumerate(lines):
        low = ln.get("text", "").lower()
        if any(m in low for m in INJECT_MARKERS):
            errors.append("L%d: line looks like prompt injection: %s" % (i+1, ln.get("text", "")[:40]))

    if require_rag_sources:
        for i, ln in enumerate(lines):
            for t_obj in ln.get("terms", []) or []:
                src = (t_obj.get("source") or "").strip()
                if not src:
                    errors.append("L%d: terms %s missing source" % (i + 1, t_obj.get("cn", "")))
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
                                errors.append("L%d: terms %s source %s not in KB" % (i + 1, t_obj.get("cn", ""), src))
            except Exception as exc:
                errors.append("RAG check failed: %s" % exc)

    return errors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--rag-sources", action="store_true")
    ap.add_argument("--rag-collection", default="leantok_kb")
    ap.add_argument("--rag-db", default=None)
    ap.add_argument("--min-lines", type=int, default=0)
    ap.add_argument("--bible", default=None)
    ap.add_argument("--episode", default=None)
    ap.add_argument("--strict-voice", action="store_true")
    args = ap.parse_args()

    rag_db = args.rag_db
    if rag_db is None:
        rag_db = os.path.join(config.workspace_root(), "assets", "rag", "chroma")
    bible = None
    if args.bible:
        with io.open(args.bible, "r", encoding="utf-8-sig") as fh:
            bible_raw = json.load(fh)
        script_id = None
        with io.open(args.script, "r", encoding="utf-8-sig") as fh:
            script_id = json.load(fh).get("id")
        bible = resolve_bible(bible_raw, script_id=script_id, episode_id=args.episode)
        if bible is None:
            print("error: could not resolve episode in bible (pass --episode matching script id)")
            sys.exit(2)
    errors, warnings = validate(args.script,
                                require_rag_sources=args.rag_sources,
                                rag_collection=args.rag_collection,
                                rag_db=rag_db,
                                min_lines=args.min_lines,
                                bible=bible,
                                strict_voice=args.strict_voice)

    if warnings:
        print("warnings (%d):" % len(warnings))
        for w in warnings:
            print("  %s" % w)
    if errors:
        print("errors (%d):" % len(errors))
        for e in errors:
            print("  %s" % e)
        print("%d errors; fix before TTS" % len(errors))
        sys.exit(1)
    elif not warnings:
        print("all checks passed")
    else:
        print("no errors, %d warnings" % len(warnings))


if __name__ == "__main__":
    main()
