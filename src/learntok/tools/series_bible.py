"""series_bible.py -- Stage 0-4 series pipeline (bible -> outline -> script -> review).

Episode count is NOT inferred from paper count. Humans approve the bible
before per-episode scripts are generated.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

from learntok import config
from learntok.tools import script_gen as sg
from learntok.tools import validate_script as vs

BIBLE_REQUIRED = (
    "series", "title", "audience", "throughline", "learning_path", "episodes",
)
EPISODE_REQUIRED = (
    "id", "title", "one_liner", "must_cover", "do_not_cover", "hook",
)


def load_json(path):
    with io.open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_json(path, data):
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def default_bible_path(series):
    return os.path.join(config.workspace_root(), "pipeline", "series", series, "bible.json")


def validate_bible(bible):
    errors = []
    for key in BIBLE_REQUIRED:
        if not bible.get(key):
            errors.append("bible missing field: %s" % key)
    eps = bible.get("episodes") or []
    if not isinstance(eps, list) or not eps:
        errors.append("bible.episodes must be a non-empty list")
        return errors
    ids = []
    for i, ep in enumerate(eps):
        if not isinstance(ep, dict):
            errors.append("episodes[%d] is not an object" % i)
            continue
        for key in EPISODE_REQUIRED:
            if not ep.get(key):
                errors.append("episodes[%d] missing %s" % (i, key))
        eid = ep.get("id")
        if eid:
            ids.append(eid)
        recap = (ep.get("recap_from_prev") or "").strip()
        if i == 0 and recap:
            errors.append("episodes[0] (%s) should not have recap_from_prev" % eid)
        if i > 0 and not recap:
            errors.append("episodes[%d] (%s) missing recap_from_prev" % (i, eid))
    if len(ids) != len(set(ids)):
        errors.append("duplicate episode ids")
    status = bible.get("status") or "draft"
    if status not in ("draft", "approved"):
        errors.append("status must be draft or approved (got %s)" % status)
    return errors


def find_episode(bible, episode_id):
    for i, ep in enumerate(bible.get("episodes") or []):
        if ep.get("id") == episode_id:
            return i, ep
    return None, None


def llm_review_passed(data):
    """LLM review passes only on an explicit JSON boolean true."""
    return isinstance(data, dict) and data.get("pass") is True


def max_sections_for_gen(explicit_max, ep, outline=None):
    """Keep a reviewed outline intact unless the caller set --max-sections."""
    if explicit_max:
        return int(explicit_max)
    n_outline = len((outline or {}).get("sections") or [])
    if n_outline:
        return n_outline
    return int((ep or {}).get("target_sections") or 8)


def episode_source_paths(ep, series):
    sources = ep.get("sources") or []
    root = config.workspace_root()
    out = []
    for src in sources:
        path = src if os.path.isabs(src) else os.path.join(root, src)
        if os.path.isdir(path) or os.path.isfile(path):
            out.append(path)
    if out:
        return out
    fallback = os.path.join(root, "materials", series or "")
    return [fallback] if os.path.isdir(fallback) else []


def bible_system_prompt():
    return (
        "You are a curriculum editor for LearnTok AI. "
        "Design a SERIES BIBLE, not one video per paper. "
        "Episode count follows a learning path, not source-file count. "
        "Return JSON only."
    )


def bible_user_prompt(series, source_text, hint_n):
    n_rule = (
        "Propose %d episodes unless the material clearly needs fewer or more." % hint_n
        if hint_n else
        "Choose the smallest episode count that still teaches the path (typically 4-8)."
    )
    return (
        "Series id: %s\nAudience: curious beginners.\n%s\n"
        "Each episode needs: id, title, one_liner, hook, must_cover[], "
        "do_not_cover[], recap_from_prev (empty for first), target_minutes, "
        "target_sections, rag_topic, sources[].\n"
        "Also include title, audience, throughline, learning_path[], "
        "status=draft, notes_for_human.\nMaterial:\n%s"
        % (series, n_rule, sg.wrap_material(source_text[:20000]))
    )


def outline_from_episode_prompt(bible, ep, source_text):
    idx, _ = find_episode(bible, ep["id"])
    prev_txt = ""
    if idx and idx > 0:
        prev = bible["episodes"][idx - 1]
        prev_txt = "Previous episode %s: %s. Recap must mention: %s" % (
            prev.get("id"), prev.get("title"), ep.get("recap_from_prev") or "")
    nsec = int(ep.get("target_sections") or 8)
    return (
        "Series: %s -- %s\nThroughline: %s\nThis episode id=%s title=%s\n"
        "One-liner: %s\nHook: %s\nMust cover: %s\nDo NOT cover: %s\n%s\n"
        "Write outline JSON with title and sections[]. Need %d sections "
        "for an 8-15 minute video. Each section: title, hook, goal, key_terms.\n"
        "Section 1 must recap previous episode if recap_from_prev is set.\nMaterial:\n%s"
        % (
            bible.get("series"), bible.get("title"), bible.get("throughline"),
            ep.get("id"), ep.get("title"), ep.get("one_liner"), ep.get("hook"),
            " / ".join(ep.get("must_cover") or []),
            " / ".join(ep.get("do_not_cover") or []),
            prev_txt, nsec, sg.wrap_material(source_text[:16000]),
        )
    )


def review_prompt(script, ep):
    return (
        "Review this dialogue script. Return JSON "
        '{"pass": true, "speaker_swaps": [], "continuity_issues": [], '
        '"clarity_gaps": [], "summary": ""}.\n'
        "A=questioner, B=explainer. B must not open confused. "
        "Gugu last line must be A.\nEpisode: %s\nRecap: %s\nMust cover: %s\nScript:\n%s"
        % (
            json.dumps({"id": ep.get("id"), "title": ep.get("title")}, ensure_ascii=False),
            ep.get("recap_from_prev") or "(first episode)",
            " / ".join(ep.get("must_cover") or []),
            json.dumps(script, ensure_ascii=False)[:12000],
        )
    )


def cmd_bible(args):
    sg._force_utf8_stdio()
    config.load_env()
    if args.approve:
        if not args.bible:
            sys.exit("error: --approve needs --bible")
        bible = load_json(args.bible)
        errs = validate_bible(bible)
        if errs:
            print("bible invalid:")
            for e in errs:
                print("  %s" % e)
            sys.exit(1)
        bible["status"] = "approved"
        save_json(args.bible, bible)
        print("approved %s (%d episodes)" % (args.bible, len(bible["episodes"])))
        return

    if args.bible and os.path.isfile(args.bible) and args.dry_run:
        bible = load_json(args.bible)
        errs = validate_bible(bible)
        print("[dry-run] bible=%s status=%s episodes=%d" % (
            args.bible, bible.get("status"), len(bible.get("episodes") or [])))
        if errs:
            for e in errs:
                print("  %s" % e)
            sys.exit(1)
        print("bible schema ok")
        return

    series = args.series
    if not series:
        sys.exit("error: --series required to generate a bible")
    sources = args.source or [os.path.join(config.workspace_root(), "materials", series)]
    out = args.bible or default_bible_path(series)
    if args.dry_run:
        print("[dry-run] would generate bible series=%s out=%s sources=%s" % (
            series, out, ", ".join(sources)))
        return

    text = sg.extract_source_text(sources, args.max_chars)
    provider, base_url, model, api_key = sg.resolve_provider(args)
    client = sg.LLMClient(provider, base_url, model, api_key, args.temperature)
    print("Stage 0: series bible (%s / %s)..." % (provider, model))
    data = client.chat_json(
        bible_system_prompt(),
        bible_user_prompt(series, text, args.max_episodes),
        4000, "series-bible",
    )
    data["series"] = series
    data["status"] = "draft"
    errs = validate_bible(data)
    if errs:
        debug = os.path.join(config.build_dir(), "series_bible_fail.json")
        save_json(debug, data)
        print("generated bible failed schema, saved %s" % debug)
        for e in errs:
            print("  %s" % e)
        sys.exit(1)
    save_json(out, data)
    print("wrote DRAFT bible %s (%d episodes). Review then approve:" % (out, len(data["episodes"])))
    print("  learntok series-bible --bible %s --approve" % out)
    for ep in data["episodes"]:
        print("  - %s  %s" % (ep.get("id"), ep.get("title")))


def cmd_outline(args):
    sg._force_utf8_stdio()
    config.load_env()
    bible = load_json(args.bible)
    errs = validate_bible(bible)
    if errs:
        for e in errs:
            print(e)
        sys.exit(1)
    idx, ep = find_episode(bible, args.episode)
    if ep is None:
        sys.exit("error: episode %s not in bible" % args.episode)
    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.bible)),
        "outline_%s.json" % ep["id"],
    )
    if args.dry_run:
        print("[dry-run] outline episode=%s out=%s" % (ep["id"], out))
        return
    sources = episode_source_paths(ep, bible.get("series"))
    if not sources:
        sys.exit("error: no sources for episode %s" % ep["id"])
    text = sg.extract_source_text(sources, args.max_chars)
    provider, base_url, model, api_key = sg.resolve_provider(args)
    client = sg.LLMClient(provider, base_url, model, api_key, args.temperature)
    pairing = sg.parse_pairing(args.characters)
    sys_prompt = sg.system_prompt(pairing)
    print("Stage 1: episode outline %s..." % ep["id"])
    min_sec = int(ep.get("target_sections") or args.min_sections or 6)
    data = client.chat_json(sys_prompt, outline_from_episode_prompt(bible, ep, text), 2500, "episode-outline")
    cap = args.max_sections or max(min_sec + 2, 10)
    data = sg.normalize_outline(data, cap)
    if len(data.get("sections") or []) < min_sec:
        sys.exit("error: outline has %d sections, need >= %d" % (len(data.get("sections") or []), min_sec))
    data["episode_id"] = ep["id"]
    data["series"] = bible.get("series")
    data["recap_from_prev"] = ep.get("recap_from_prev") or ""
    save_json(out, data)
    print("wrote %s (%d sections)" % (out, len(data["sections"])))


def cmd_review(args):
    sg._force_utf8_stdio()
    config.load_env()
    script = load_json(args.script)
    bible = load_json(args.bible) if args.bible else {"episodes": []}
    ep = {}
    if args.bible:
        if args.episode:
            _, ep = find_episode(bible, args.episode)
        else:
            _, ep = find_episode(bible, script.get("id"))
        ep = ep or {}
    errors, warnings = vs.validate(
        args.script,
        require_rag_sources=args.rag_sources,
        min_lines=args.min_lines,
        bible={"episode": ep} if ep else None,
        strict_voice=True,
    )
    print("rule gate: %d errors, %d warnings" % (len(errors), len(warnings)))
    for e in errors:
        print("  E %s" % e)
    for w in warnings:
        print("  W %s" % w)
    if args.dry_run:
        sys.exit(1 if errors else 0)
    provider, base_url, model, api_key = sg.resolve_provider(args)
    client = sg.LLMClient(provider, base_url, model, api_key, 0.2)
    print("LLM review (%s / %s)..." % (provider, model))
    data = client.chat_json(
        "You are a strict dialogue editor. Return JSON only.",
        review_prompt(script, ep),
        2000, "script-review",
    )
    out = args.out or os.path.join(config.build_dir(), "review_%s.json" % (script.get("id") or "script"))
    save_json(out, {"rule_errors": errors, "rule_warnings": warnings, "llm": data})
    print("wrote %s  pass=%s" % (out, data.get("pass")))
    if errors or not llm_review_passed(data):
        sys.exit(1)


def cmd_gen(args):
    sg._force_utf8_stdio()
    config.load_env()
    bible = load_json(args.bible)
    errs = validate_bible(bible)
    if errs:
        for e in errs:
            print(e)
        sys.exit(1)
    if bible.get("status") != "approved" and not args.force:
        sys.exit("error: bible status=%s; human must --approve (or pass --force)" % bible.get("status"))
    _, ep = find_episode(bible, args.episode)
    if ep is None:
        sys.exit("error: episode %s not in bible" % args.episode)
    sources = list(args.source or []) + episode_source_paths(ep, bible.get("series"))
    # unique preserve order
    seen, uniq = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    sources = uniq
    if not sources:
        sys.exit("error: no sources")
    out = args.out or os.path.join(
        config.workspace_root(), "pipeline", "examples", "script_%s.json" % ep["id"]
    )
    min_lines = args.min_lines if args.min_lines else max(90, int(ep.get("target_minutes") or 10) * 8)
    header = (
        "[SERIES BIBLE]\nseries=%s\nthroughline=%s\nepisode=%s %s\n"
        "one_liner=%s\nhook=%s\nmust_cover=%s\ndo_not_cover=%s\nrecap=%s\n"
        "Write a LONG episode (target %s minutes, %s sections). "
        "A opens if the hook is a confused question. B never says gugu.\n[END BIBLE]\n\n"
        % (
            bible.get("series"), bible.get("throughline"),
            ep.get("id"), ep.get("title"), ep.get("one_liner"), ep.get("hook"),
            " / ".join(ep.get("must_cover") or []),
            " / ".join(ep.get("do_not_cover") or []),
            ep.get("recap_from_prev") or "(first episode)",
            ep.get("target_minutes") or 12,
            ep.get("target_sections") or 8,
        )
    )
    tmp = os.path.join(config.build_dir(), ".bible_%s.md" % ep["id"])
    os.makedirs(config.build_dir(), exist_ok=True)
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header)
    gen_argv = ["--source", tmp] + list(sources)
    default_outline = os.path.join(
        os.path.dirname(os.path.abspath(args.bible)),
        "outline_%s.json" % ep["id"],
    )
    outline = load_json(default_outline) if os.path.isfile(default_outline) else None
    if outline is not None:
        print("using reviewed outline %s" % default_outline)
    max_sections = max_sections_for_gen(args.max_sections, ep, outline)
    gen_argv.extend([
        "--id", ep["id"],
        "--title", ep.get("title") or ep["id"],
        "--series", bible.get("series") or "",
        "--max-sections", str(max_sections),
        "--max-chars", str(args.max_chars),
        "--out", out,
        "--characters", args.characters,
        "--provider", args.provider,
    ])
    if outline is not None:
        gen_argv.extend(["--outline", default_outline])
    if ep.get("rag_topic"):
        gen_argv.extend(["--rag-topic", ep["rag_topic"]])
    if args.model:
        gen_argv.extend(["--model", args.model])
    if args.base_url:
        gen_argv.extend(["--base-url", args.base_url])
    if args.api_key:
        gen_argv.extend(["--api-key", args.api_key])
    if args.no_rag_sources:
        gen_argv.append("--no-rag-sources")
    if args.dry_run:
        gen_argv.append("--dry-run")
    print("Stage 2: script-gen %s -> %s (min_lines=%d, max_sections=%d)" % (
        ep["id"], out, min_lines, max_sections))
    old = sys.argv
    try:
        sys.argv = ["script_gen.py"] + gen_argv
        sg.main()
    finally:
        sys.argv = old
    if args.dry_run:
        return
    if not os.path.isfile(out):
        sys.exit("error: script-gen did not write %s" % out)
    errors, warnings = vs.validate(
        out, min_lines=min_lines, bible={"episode": ep}, strict_voice=True,
        require_rag_sources=not args.no_rag_sources,
    )
    print("post-gen gate: %d errors, %d warnings" % (len(errors), len(warnings)))
    for e in errors:
        print("  E %s" % e)
    for w in warnings[:12]:
        print("  W %s" % w)
    if errors:
        sys.exit(1)
    print("next: learntok script-review --script %s --bible %s --episode %s" % (
        out, args.bible, ep["id"]))


def _add_llm_flags(ap):
    ap.add_argument("--provider", choices=["auto", "deepseek", "local"], default="auto")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--max-chars", type=int, default=18000)
    ap.add_argument("--characters", default="A=%s,B=%s" % (sg.DEFAULT_PAIRING["A"], sg.DEFAULT_PAIRING["B"]))
    ap.add_argument("--dry-run", action="store_true")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = os.environ.get("LEARNTOK_SERIES_ACTION")
    if action is None:
        if argv and argv[0] in ("bible", "outline", "gen", "review"):
            action = argv.pop(0)
        else:
            action = "bible"
    ap = argparse.ArgumentParser(description="LearnTok series bible pipeline")
    ap.add_argument("--bible", default=None)
    ap.add_argument("--series", default=None)
    ap.add_argument("--source", action="append", default=None)
    ap.add_argument("--episode", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-episodes", type=int, default=0)
    ap.add_argument("--max-sections", type=int, default=0)
    ap.add_argument("--min-sections", type=int, default=6)
    ap.add_argument("--min-lines", type=int, default=0)
    ap.add_argument("--rag-sources", action="store_true")
    ap.add_argument("--no-rag-sources", action="store_true")
    ap.add_argument("--script", default=None)
    _add_llm_flags(ap)
    args = ap.parse_args(argv)
    args.rag_sources = bool(args.rag_sources) and not args.no_rag_sources
    if action == "bible":
        return cmd_bible(args)
    if action == "outline":
        if not args.bible or not args.episode:
            sys.exit("error: series-outline needs --bible and --episode")
        return cmd_outline(args)
    if action == "gen":
        if not args.bible or not args.episode:
            sys.exit("error: series-gen needs --bible and --episode")
        return cmd_gen(args)
    if action == "review":
        if not args.script:
            sys.exit("error: script-review needs --script")
        return cmd_review(args)
    sys.exit("error: unknown series action %s" % action)


if __name__ == "__main__":
    main()
