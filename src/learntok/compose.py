#!/usr/bin/env python3
"""LearnTok AI compositing pipeline.

script JSON + asset library (manifest) -> vertical short-video via ffmpeg.
Two passes: (A) voiceover mix from per-line TTS files, (B) background concat
+ ASS subtitle burn + audio mix.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import unicodedata
import wave

from learntok import config

DEFAULT_CHARACTERS = {
    "A": {"name": "企鵝燈", "role": "questioner", "color": "#FFD54F"},
    "B": {"name": "熊大", "role": "explainer", "color": "#81C784"},
}

# Pre-recorded SFX replacements: text pattern -> audio file (relative to assets_root).
# Merged into the line audio at compose time (see _line_audio_path) so the flow
# works with or without RVC, and re-runs stay idempotent (source files untouched).
SFX_REPLACEMENTS = {
    "咕咕嘎嘎": "audio/sfx/gugu_gaga.mp3",
}


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def find_tool(name):
    return config.find_tool(name)


def run(cmd, dry_run):
    if dry_run:
        printable = " ".join(cmd)
        print("[dry-run] " + printable[:500] + (" ..." if len(printable) > 500 else ""))
        return
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        sys.exit("ffmpeg failed (%s):\n%s" % (cmd[0], proc.stderr[-3000:]))


def ass_color(hex_color):
    c = str(hex_color).strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        raise ValueError("invalid ASS color %r (expected #RRGGBB)" % (hex_color,))
    r, g, b = c[0:2].upper(), c[2:4].upper(), c[4:6].upper()
    return "&H00%s%s%s" % (b, g, r)


def ass_time(sec):
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return "%d:%02d:%05.2f" % (h, m, s)



_SPEAKER_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")
_ASS_CONTROL_CHARS = {"{": "\uff5b", "}": "\uff5d", "\\": "\uff3c"}


def sanitize_ass_text(text):
    """Neutralize ASS override-tag delimiters so line text cannot inject tags."""
    out = str(text)
    for ch, repl in _ASS_CONTROL_CHARS.items():
        out = out.replace(ch, repl)
    return out


def validate_speaker_keys(characters):
    """Reject speaker keys that could corrupt ASS style names / event fields."""
    for key in characters:
        if not _SPEAKER_KEY_RE.fullmatch(str(key)):
            raise ValueError(
                "invalid speaker key %r (expected ^[A-Za-z0-9_]+$)" % (key,)
            )


def sanitize_slug(value, fallback="video"):
    """Sanitize a script id / speaker token to [A-Za-z0-9_-] for paths/filenames."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value)).strip("_")
    return cleaned or fallback


def filter_arg_quote(text):
    """Quote a string as a single-quoted ffmpeg filtergraph argument."""
    escaped = str(text).replace("\\", "\\\\").replace("'", "\\'")
    return "'%s'" % escaped


def asset_path(assets_root, rel):
    """Resolve an asset-relative path under assets_root, refusing escapes."""
    root = os.path.realpath(assets_root)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep):
        raise ValueError("asset path escapes assets root: %s" % rel)
    return full


def _char_width_ratio(ch):
    """Display-width ratio: 1.0 for full-width (CJK), 0.5 for half-width (ASCII)."""
    w = unicodedata.east_asian_width(ch)
    return 1.0 if w in ("W", "F", "A") else 0.5


def wrap_ass_text(text, size, avail_width):
    r"""Wrap text to fit avail_width pixels, inserting ASS line breaks (\\N).

    Breaks at whitespace word boundaries so English words are never split
    mid-word; falls back to per-character wrapping only when a single token
    (e.g. a long CJK run) is wider than the line.
    """
    safety = avail_width * 0.97
    space_w = _char_width_ratio(" ") * size

    def _line_width(s):
        return sum(_char_width_ratio(ch) for ch in s) * size

    def _char_wrap(tok):
        out = []
        cur, cur_w = "", 0.0
        for ch in tok:
            cw = _char_width_ratio(ch) * size
            if cur and cur_w + cw > safety:
                out.append(cur)
                cur, cur_w = ch, cw
            else:
                cur += ch
                cur_w += cw
        if cur:
            out.append(cur)
        return out

    out_lines = []
    for seg in text.split("\\N"):
        if not seg:
            out_lines.append("")
            continue
        tokens = seg.split()
        cur, cur_w = "", 0.0
        for tok in tokens:
            tw = _line_width(tok)
            if tw > safety:
                # single overlong run (rare): flush current line, then wrap it
                if cur:
                    out_lines.append(cur)
                    cur, cur_w = "", 0.0
                chunks = _char_wrap(tok)
                for ch_ in chunks[:-1]:
                    out_lines.append(ch_)
                cur, cur_w = chunks[-1], _line_width(chunks[-1])
                continue
            gap = space_w if cur else 0.0
            if cur and cur_w + gap + tw <= safety:
                cur += " " + tok
                cur_w += gap + tw
            elif cur:
                out_lines.append(cur)
                cur, cur_w = tok, tw
            else:
                cur, cur_w = tok, tw
        if cur:
            out_lines.append(cur)
    return "\\N".join(out_lines)


def wrap_terms_ass(eng_pairs, size, avail_width):
    r"""Render structured terms as an ASS block: ONE term per row, with its
    Chinese and English kept together on the same row (\\N never splits a term).

    The font size is computed uniformly so that even the widest term fits on a
    single row; this guarantees no cn/en pair is ever broken across lines.
    """
    safety = avail_width * 0.97
    min_size = 14

    def _w(text, sz):
        return sum(_char_width_ratio(ch) for ch in text) * sz

    blocks = [("%s %s" % (cn, en)).strip() for cn, en in eng_pairs if (cn or en)]
    if not blocks:
        return ""
    widest = max(blocks, key=lambda b: _w(b, size))
    line_size = size
    while line_size > min_size and _w(widest, line_size) > safety:
        line_size -= 1
    grey = "\\1c&HAAAAAA&"
    return "\\N".join("{\\fs%d%s}%s" % (line_size, grey, b) for b in blocks)

# Stopwords that lead into an annotated term (前綴功能詞).
# Backward maximum matching: walk from the parenthesis toward the start,
# collecting content chars until a stopword is hit -> that boundary marks
# the real term start. Set is deliberately conservative to avoid
# over-stripping real term prefixes (e.g. "被" excluded -> "被動收入" kept;
# "外部" excluded -> "外部董事" kept).
_TERM_STOP = frozenset({
    # single-char lead-in verbs (low risk of being a term prefix)
    "像", "叫", "怕",
    # prepositions / case markers
    "透過", "通過", "藉由", "經由",
    # modal / lead-in verbs
    "就是", "這就是", "這是", "可以", "應該", "叫做", "稱為", "稱作",
    "比如", "例如", "像是", "另外", "比如說",
    # conjunctions / adverbs
    "所以", "因為", "如果", "雖然", "但是", "而且", "並且", "還有", "還要",
    # demonstratives / question
    "這", "那", "這個", "那個", "什麼", "為什麼", "怎麼",
    # quantifier / modifier lead-ins
    "任何", "所有", "某些", "其他", "所謂",
})


def _extract_cn_term(run):
    """Given the Chinese run immediately before an (English) parenthesis,
    return just the term portion by stripping trailing lead-in stopwords
    via backward maximum matching against _TERM_STOP."""
    if not run:
        return run
    i = len(run)
    term_chars = []
    max_stop = max((len(w) for w in _TERM_STOP), default=1)
    while i > 0:
        matched = None
        for wlen in range(min(max_stop, i), 0, -1):
            if run[i - wlen:i] in _TERM_STOP:
                matched = run[i - wlen:i]
                break
        if matched:
            break
        term_chars.append(run[i - 1])
        i -= 1
    term = "".join(reversed(term_chars))
    # safety: never return an empty / single-char term; fall back to tail
    if len(term) < 2:
        term = run[-max(2, len(run)):] if run else run
    return term

def build_ass(lines, characters, out_path, width, height, margin_v):
    style_fmt = ("Style: {name},Microsoft YaHei,{size},{color},&H000000FF,&H00000000,&H80000000,"
                 "-1,0,0,0,100,100,0,0,1,{outline},0,2,{mh},{mh},{mv},1")
    margin_h = 30
    size = max(40, width // 12)
    outline = max(2, width // 200)
    avail_width = width - 2 * margin_h
    parts = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % width,
        "PlayResY: %d" % height,
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
         " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
         " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"),
    ]
    validate_speaker_keys(characters)
    style_of = {}
    for key, ch in characters.items():
        style = "spk%s" % key
        style_of[key] = style
        parts.append(style_fmt.format(name=style, size=size,
                                      color=ass_color(ch.get("color", "#FFFFFF")),
                                      outline=outline, mh=margin_h, mv=margin_v))
    parts.append(style_fmt.format(name="spkdefault", size=size, color="&H00FFFFFF",
                                  outline=outline, mh=margin_h, mv=margin_v))
    # English subtitle style: smaller font, grey, same alignment
    eng_size = max(20, width // 22)
    parts.append(("Style: spkeng,Microsoft YaHei,{esize},&H00AAAAAA,&H000000FF,&H00000000,&H80000000,"
                   "-1,0,0,0,100,100,0,0,1,{outline},0,2,{mh},{mh},{mv},1").format(
                   esize=eng_size, outline=outline, mh=margin_h, mv=margin_v))
    parts += ["", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    import re as _re
    for ln in lines:
        speaker = ln.get("speaker") or ""
        style = style_of.get(speaker, "spkdefault")
        raw_text = sanitize_ass_text(ln["text"]).replace("\r", " ").replace("\n", " ")
        # Split English parentheticals from Chinese text
        # Capture Chinese term before parenthesis: 公司治理（Corporate Governance）
        # Sub line shows: "公司治理 Corporate Governance" (Chinese + English side by side)
        # Prefer structured terms: [{cn, en}] when present (root-cause fix,
        # no regex term-boundary guessing). Fall back to inline-paren inference.
        terms = ln.get("terms")
        if terms:
            cn_text = _re.sub(r"[（(][A-Za-z][^）)]*[）)]", "", raw_text).strip()
            cn_text = _re.sub(r" {2,}", " ", cn_text).strip()
            eng_pairs = [(sanitize_ass_text(t.get("cn", "")), sanitize_ass_text(t.get("en", "")))
                         for t in terms if t.get("en")]
        else:
            eng_pairs = _re.findall(r"([\u4e00-\u9fff]+)[（(]([A-Za-z][^）)]*)[）)]", raw_text)
            eng_pairs = [(_extract_cn_term(cn), en) for cn, en in eng_pairs]
            cn_text = _re.sub(r"[（(][A-Za-z][^）)]*[）)]", "", raw_text).strip()
            cn_text = _re.sub(r" {2,}", " ", cn_text).strip()
        if eng_pairs:
            # Main Chinese line (word-boundary wrap) + a terms block below:
            # each structured term on its own row, cn+en never split.
            cn_wrapped = wrap_ass_text(cn_text, size, avail_width)
            eng_block = wrap_terms_ass(eng_pairs, max(20, width // 22), avail_width)
            text = cn_wrapped + "\\N" + eng_block
        else:
            text = wrap_ass_text(cn_text, size, avail_width)
        parts.append("Dialogue: 0,%s,%s,%s,,0,0,0,,%s"
                     % (ass_time(ln["start"]), ass_time(ln["end"]), style, text))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


def make_silence(path, seconds, rate=44100):
    frames = int(seconds * rate)
    chunk = b"\x00\x00\x00\x00" * rate
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        full, rem = divmod(frames, rate)
        for _ in range(full):
            wf.writeframes(chunk)
        if rem:
            wf.writeframes(chunk[: rem * 4])


def _line_audio_path(ln, idx, ffmpeg, assets_root, build_dir, dry_run):
    """Resolve a line's audio path, appending the pre-recorded SFX (e.g. 咕咕嘎嘎)
    when the line carries one. Merged at compose time so the flow works without
    RVC and re-runs are idempotent (source audio files stay untouched)."""
    base = asset_path(assets_root, ln["audio_file"]).replace("\\", "/")
    sfx_key = ln.get("_sfx")
    if not sfx_key:
        for pat in SFX_REPLACEMENTS:
            if pat in ln.get("text", ""):
                sfx_key = pat
                break
    if not sfx_key or sfx_key not in SFX_REPLACEMENTS:
        return base
    sfx_path = asset_path(assets_root, SFX_REPLACEMENTS[sfx_key]).replace("\\", "/")
    if not os.path.isfile(sfx_path):
        print("warning: SFX not found: %s" % sfx_path)
        return base
    out = os.path.join(build_dir, "sfx_line%04d.mp3" % idx)
    run([ffmpeg, "-y", "-i", base, "-i", sfx_path,
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
         "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", out], dry_run)
    return out


def render_voiceover(ffmpeg, lines, assets_root, build_dir, total_dur, dry_run, gain_db_map=None):
    vo_path = os.path.join(build_dir, "voiceover.wav")
    timed = [(i, ln) for i, ln in enumerate(lines) if ln.get("audio_file")]
    if not timed:
        make_silence(vo_path, total_dur)
        print("voiceover: no per-line audio in script; generated %.1fs silence" % total_dur)
        return vo_path
    chunk = 120
    groups = [timed[i:i + chunk] for i in range(0, len(timed), chunk)]
    group_files = []
    for gi, group in enumerate(groups):
        gpath = os.path.join(build_dir, "vo_group%02d.wav" % gi)
        inputs, filt = [], []
        for li, (idx, ln) in enumerate(group):
            apath = _line_audio_path(ln, idx, ffmpeg, assets_root, build_dir, dry_run)
            inputs += ["-i", apath]
            delay = int(round(float(ln["start"]) * 1000))
            gain_db = float((gain_db_map or {}).get(ln.get("speaker", ""), 0.0) or 0.0)
            vol_filter = ",volume=%.4f" % (10 ** (gain_db / 20.0)) if abs(gain_db) > 0.001 else ""
            filt.append("[%d:a]aresample=44100,aformat=channel_layouts=stereo%s,adelay=%d|%d[a%d]"
                        % (li, vol_filter, delay, delay, li))
        mix_in = "".join("[a%d]" % li for li in range(len(group)))
        filt.append("%samix=inputs=%d:duration=longest:normalize=0,atrim=0:%.2f,alimiter=limit=0.891[out]"
                    % (mix_in, len(group), total_dur))
        ffs = os.path.join(build_dir, "vo_group%02d.ffs" % gi)
        with open(ffs, "w", encoding="utf-8") as fh:
            fh.write(";\n".join(filt))
        run([ffmpeg, "-y"] + inputs + ["-filter_complex_script", ffs, "-map", "[out]", gpath], dry_run)
        group_files.append(gpath)
    if len(group_files) == 1:
        if not dry_run:
            shutil.move(group_files[0], vo_path)
        return vo_path
    inputs, refs = [], []
    for gi, gpath in enumerate(group_files):
        inputs += ["-i", gpath.replace("\\", "/")]
        refs.append("[%d:a]" % gi)
    ffs = os.path.join(build_dir, "vo_final.ffs")
    with open(ffs, "w", encoding="utf-8") as fh:
        fh.write("%samix=inputs=%d:duration=longest:normalize=0,atrim=0:%.2f,alimiter=limit=0.891[out]"
                    % ("".join(refs), len(group_files), total_dur))
    run([ffmpeg, "-y"] + inputs + ["-filter_complex_script", ffs, "-map", "[out]", vo_path], dry_run)
    return vo_path


def plan_backgrounds(backgrounds, total_dur, seed):
    pool = [b for b in backgrounds if float(b.get("duration", 0)) > 0]
    rng = random.Random() if seed is None else random.Random(seed)
    plan, covered = [], 0.0
    while covered < total_dur and pool:
        batch = pool[:]
        rng.shuffle(batch)
        for item in batch:
            dur = float(item["duration"])
            slack = max(0.0, dur - 20.0)
            offset = round(rng.uniform(0.0, slack), 2) if slack else 0.0
            speed = round(rng.uniform(0.92, 1.08), 3)
            take = (dur - offset) / speed
            plan.append({"file": item["file"], "offset": offset, "speed": speed,
                         "mirror": rng.random() < 0.5, "take": take,
                         "fit": item.get("fit", "crop")})
            covered += take
            if covered >= total_dur:
                break
    return plan


def render_video(ffmpeg, args, script, manifest, build_dir, vo_path, total_dur, width, height):
    dry_run = args.dry_run
    characters = dict(DEFAULT_CHARACTERS)
    characters.update(script.get("characters", {}))
    ass_path = os.path.join(build_dir, "subtitles.ass")
    build_ass(script["lines"], characters, ass_path, width, height, 620)

    input_args = []
    counter = -1

    def add_input(*tokens):
        nonlocal counter
        input_args.extend(tokens)
        counter += 1
        return counter

    filt = []
    plan = plan_backgrounds(manifest.get("backgrounds", []), total_dur, args.seed)
    if plan:
        labels = []
        for bi, item in enumerate(plan):
            bpath = asset_path(args.assets_root, item["file"]).replace("\\", "/")
            bi_idx = add_input("-i", bpath)
            chain = "[%d:v]trim=start=%s,setpts=(PTS-STARTPTS)/%s" % (bi_idx, item["offset"], item["speed"])
            if item["mirror"]:
                chain += ",hflip"
            if item.get("fit", "crop") == "blur":
                filt.append(chain + ",split[bg%da][bg%db]" % (bi, bi))
                filt.append(("[bg%da]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
                             "boxblur=18:3[bl%d]") % (bi, width, height, width, height, bi))
                filt.append("[bg%db]scale=%d:-2[fg%d]" % (bi, width, bi))
                filt.append("[bl%d][fg%d]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v%d]" % (bi, bi, bi))
            else:
                chain += (",scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,fps=30[v%d]"
                          % (width, height, width, height, bi))
                filt.append(chain)
            labels.append("[v%d]" % bi)
        filt.append("%sconcat=n=%d:v=1:a=0[bg]" % ("".join(labels), len(plan)))
    else:
        ci = add_input("-f", "lavfi", "-i", "color=c=0x101418:s=%dx%d:r=30" % (width, height))
        filt.append("[%d:v]trim=duration=%.2f[bg]" % (ci, total_dur))
        print("warning: manifest has no backgrounds; using solid-color placeholder")

    # Load avatars by character name from characters.json, filtered to script's characters
    avatars = []
    char_cfg_path = os.path.join(args.assets_root, "characters.json")
    if os.path.isfile(char_cfg_path):
        with open(char_cfg_path, "r", encoding="utf-8-sig") as fh:
            char_cfg = json.load(fh)
        # Side/layout come from character config (avatar_side); A/B position is the fallback
        for spk_key, side in [("A", "left"), ("B", "right")]:
            char_entry = script.get("characters", {}).get(spk_key, {})
            name = char_entry.get("name", "")
            if name and name in char_cfg:
                av_file = char_cfg[name].get("avatar")
                if av_file:
                    try:
                        apath = asset_path(args.assets_root, av_file)
                    except ValueError as e:
                        print("warning: %s" % e)
                        continue
                    if os.path.isfile(apath):
                        av = {"side": side, "file": av_file}
                        av_side = char_cfg[name].get("avatar_side")
                        if av_side in ("left", "right"):
                            av["side"] = av_side
                        for layout_key in ("width_ratio", "margin_ratio", "y_ratio"):
                            if layout_key in char_cfg[name]:
                                av[layout_key] = char_cfg[name][layout_key]
                        if char_cfg[name].get("flip"):
                            av["flip"] = True
                        avatars.append((av, apath.replace("\\", "/")))
                    else:
                        print("warning: avatar not found for %s: %s" % (name, apath))
    if avatars:
        print("avatars: %d corner overlay(s)" % len(avatars))

    ass_ref = ass_path.replace("\\", "/")
    filt.append("[bg]ass=%s[%s]" % (filter_arg_quote(ass_ref), "vbase" if avatars else "vout"))
    prev = "vbase"
    for ai, (av, apath) in enumerate(avatars):
        a_idx = add_input("-loop", "1", "-i", apath)
        aw = max(2, int(round(width * float(av.get("width_ratio", 0.27)) / 2)) * 2)
        chain = "[%d:v]scale=%d:-2,format=rgba" % (a_idx, aw)
        if av.get("flip"):
            chain += ",hflip"
        filt.append(chain + "[av%d]" % ai)
        margin = int(round(width * float(av.get("margin_ratio", 0.035))))
        y = int(round(height * float(av.get("y_ratio", 0.085))))
        x = "W-w-%d" % margin if av.get("side") == "right" else str(margin)
        out_label = "vout" if ai == len(avatars) - 1 else "vav%d" % ai
        filt.append("[%s][av%d]overlay=%s:%d[%s]" % (prev, ai, x, y, out_label))
        prev = out_label

    vo_idx = add_input("-i", vo_path.replace("\\", "/"))
    bgm_list = manifest.get("bgm", [])
    if bgm_list:
        # BGM: random pick per render（預設隨機；有 --seed 才固定可重現）。
        # Volume comes from the track's manifest calibration so every track mixes
        # at the same level vs voice (stable balance across BGM).
        if args.seed is None:
            b_conf = random.choice(bgm_list)
        else:
            bgm_rng = random.Random(args.seed * 1000003 + 7919)
            b_conf = bgm_rng.choice(bgm_list)
        bpath = asset_path(args.assets_root, b_conf["file"]).replace("\\", "/")
        b_idx = add_input("-stream_loop", "-1", "-i", bpath)
        vol = float(b_conf.get("volume", 0.08))
        filt.append("[%d:a]volume=%.3f[bgm]" % (b_idx, vol))
        filt.append("[%d:a]aformat=channel_layouts=stereo,atrim=0:%.2f[vo]" % (vo_idx, total_dur))
        filt.append("[vo][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
        print("bgm: %s @ %.0f%% volume" % (os.path.basename(bpath), vol * 100))
    else:
        filt.append("[%d:a]aformat=channel_layouts=stereo,atrim=0:%.2f[aout]" % (vo_idx, total_dur))

    ffs = os.path.join(build_dir, "compose.ffs")
    with open(ffs, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(filt))

    cmd = [ffmpeg, "-y"] + input_args + [
        "-filter_complex_script", ffs,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-r", "30", "-t", "%.2f" % total_dur, args.out]
    run(cmd, dry_run)
    if dry_run:
        print("artifacts: %s" % build_dir)
        print(" - subtitles.ass (%d lines)" % len(script["lines"]))
        print(" - compose.ffs (filter graph)")
        print(" - voiceover.wav")
        if plan:
            print("background plan: %d clips to cover %.1fs" % (len(plan), total_dur))
            for item in plan[:5]:
                flags = (" [mirrored]" if item["mirror"] else "")
                if item.get("fit") == "blur":
                    flags += " [fit=blur]"
                print("   %s @%.1fs x%.3f%s" % (os.path.basename(item["file"]), item["offset"],
                                                item["speed"], flags))
            if len(plan) > 5:
                print("   ...")


def main():
    ap = argparse.ArgumentParser(description="LearnTok AI: asset-library + ffmpeg compositing pipeline")
    ap.add_argument("--script", required=True, help="script JSON (dialogue + timing)")
    ap.add_argument("--manifest", default=os.path.join("assets", "manifest.json"))
    ap.add_argument("--assets-root", default="assets")
    ap.add_argument("--out", default=None)
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--seed", type=int, default=None, help="背景/BGM 隨機種子（預設 None＝每次隨機）")
    ap.add_argument("--max-duration", type=float, default=0.0, help="cut script at N seconds (testing)")
    ap.add_argument("--dry-run", action="store_true", help="write artifacts and print commands only")
    args = ap.parse_args()

    script = load_json(args.script)
    manifest = load_json(args.manifest)
    if args.max_duration > 0:
        script["lines"] = [ln for ln in script["lines"] if float(ln["start"]) < args.max_duration]
        for ln in script["lines"]:
            ln["end"] = min(float(ln["end"]), args.max_duration)
    if not script.get("lines"):
        sys.exit("error: script has no dialogue lines")
    total_dur = max(float(ln["end"]) for ln in script["lines"]) + 2.0
    if total_dur > 600.0:
        sys.exit("error: script total_dur %.1fs exceeds the 600s (10 min) cap; "
                 "split the script or use --max-duration" % total_dur)
    width, height = (int(x) for x in script.get("resolution", "720x1280").split("x"))
    slug = sanitize_slug(script.get("id", "video"))
    args.out = args.out or os.path.join("output", "out_%s_v01.mp4" % slug)
    build_dir = args.build_dir or os.path.join("pipeline", "build", slug)
    os.makedirs(build_dir, exist_ok=True)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    seed_label = "random" if args.seed is None else str(args.seed)
    print("plan: %d lines, total %.1fs, %dx%d, seed %s"
          % (len(script["lines"]), total_dur, width, height, seed_label))
    ffmpeg = "ffmpeg" if args.dry_run else find_tool("ffmpeg")
    char_cfg = load_json(os.path.join(args.assets_root, "characters.json"))
    gain_db_map = {}
    for key, ch in script.get("characters", {}).items():
        gain_db_map[key] = float(char_cfg.get(ch.get("name", ""), {}).get("voice_gain_db", 0.0) or 0.0)
    vo = render_voiceover(ffmpeg, script["lines"], args.assets_root, build_dir, total_dur, args.dry_run, gain_db_map)
    render_video(ffmpeg, args, script, manifest, build_dir, vo, total_dur, width, height)
    print("dry-run complete" if args.dry_run else "done: %s" % args.out)


if __name__ == "__main__":
    main()
