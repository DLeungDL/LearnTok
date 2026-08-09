#!/usr/bin/env python3
"""Generate per-line TTS audio via edge-tts, then repack line timings.

Requires: pip install edge-tts, and ffprobe on PATH (bundled with ffmpeg).
Writes mp3 files into assets/audio/lines/ and updates the script JSON with
audio_file fields plus fresh start/end times (sequential packing).

Voice/rate settings are read from assets/characters.json by character name.
The script JSON's characters[A/B].name determines which voice to use.

Text preprocessing:
- Strips English parentheticals before TTS (English stays in JSON for subtitles)
- Per-character speech style preprocessing (e.g. 派大星: halting, flat, trailing)

SFX replacement:
- Lines containing onomatopoeia memes (e.g. 咕咕嘎嘎) are split:
  TTS generates the spoken part, then compose.py appends the pre-recorded SFX via ffmpeg.

Audio cache:
- Each line's TTS output is cached by (tts_text, voice, rate) hash.
- If only layout, subtitles, backgrounds, or BGM change, cached audio is reused
  without calling Edge-TTS again.
- Use --no-cache to force regenerate all lines.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from learntok import config
from learntok.compose import SFX_REPLACEMENTS, sanitize_slug  # single source of truth (compose.py)
GAP = 0.12
LEAD = 0.10


def load_characters(assets_root):
    """Load character config from assets/characters.json."""
    path = os.path.join(assets_root, "characters.json")
    if not os.path.isfile(path):
        sys.exit("error: %s not found" % path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def resolve_voice(speaker_key, script, char_cfg):
    """Resolve TTS voice + rate from script JSON character name -> characters.json."""
    name = script.get("characters", {}).get(speaker_key, {}).get("name", "")
    if name and name in char_cfg:
        cfg = char_cfg[name]
        return cfg.get("tts_voice"), cfg.get("tts_rate"), name
    return None, None, ""


def strip_english(text):
    """Remove English parentheticals for TTS."""
    text = re.sub(r'\uff08[A-Za-z][^\uff09]*\uff09', '', text)
    text = re.sub(r'\([A-Za-z][^)]*\)', '', text)
    text = re.sub(r' {2,}', ' ', text).strip()
    return text


def preprocess_paidaxing(text):
    """派大星 speech preprocessing — slow, halting, flat intonation."""
    text = text.replace('！', '。')
    text = text.replace('？', '。')
    text = text.replace('……', '——')
    text = text.replace('，', ' ， ')
    text = text.replace('。', ' 。 ')
    for word in ['所以', '就像', '那', '嗯', '呃', '啊', '對', '不過', '但是']:
        text = text.replace(word, word + ' ')
    text = text.rstrip()
    if not text.endswith('——'):
        text = text + '——'
    text = re.sub(r' {2,}', ' ', text).strip()
    return text


def split_sfx(text):
    """Split text into (spoken_part, sfx_key) if it contains a SFX pattern.
    Returns (text_without_sfx, sfx_key) or (original_text, None)."""
    for key in sorted(SFX_REPLACEMENTS.keys(), key=len, reverse=True):
        if key in text:
            spoken = text.replace(key, '').strip()
            spoken = re.sub(r'[，。！？\s]+$', '', spoken)
            spoken = re.sub(r'^[…\s]+', '', spoken)
            return spoken, key
    return text, None


def preprocess_text(text, character_name):
    """Apply character-specific TTS text preprocessing."""
    text = strip_english(text)
    if character_name == '派大星':
        text = preprocess_paidaxing(text)
    return text


def cache_key(tts_text, voice, rate):
    """Generate a hash-based cache key for a TTS line."""
    raw = "|".join([tts_text, voice, rate or ""])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def find_ffmpeg():
    return config.find_tool("ffmpeg", exit_on_missing=False)


def concat_audio(ffmpeg, parts, out_path):
    """Concatenate multiple audio files using ffmpeg filter_complex concat."""
    if len(parts) == 1:
        shutil.copy2(parts[0], out_path)
        return
    inputs = []
    for p in parts:
        inputs.extend(["-i", p])
    filter_parts = "".join("[%d:a]" % i for i in range(len(parts)))
    filter_str = "%sconcat=n=%d:v=0:a=1[out]" % (filter_parts, len(parts))
    cmd = [ffmpeg, "-y"] + inputs + ["-filter_complex", filter_str,
           "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        print("  concat error: %s" % result.stderr[-500:])
        shutil.copy2(parts[0], out_path)


def probe_duration(path):
    ffprobe = config.find_tool("ffprobe", exit_on_missing=False)
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, errors="replace")
        try:
            return float(out.stdout.strip())
        except ValueError:
            pass
    size = os.path.getsize(path)
    return max(0.8, size / 16000.0)


async def synth_all(script, out_dir, rate, slug, char_cfg, assets_root, proxy=None, retries=3, use_cache=True):
    import edge_tts
    sem = asyncio.Semaphore(4)
    ffmpeg = find_ffmpeg()

    # Cache directory: assets/audio/cache/<slug>/
    cache_dir = os.path.join(assets_root, "audio", "cache", slug)
    if use_cache:
        os.makedirs(cache_dir, exist_ok=True)

    cache_hits = 0
    cache_misses = 0

    async def one(idx, line):
        nonlocal cache_hits, cache_misses
        async with sem:
            speaker = line.get("speaker") or "A"
            voice, spk_rate, char_name = resolve_voice(speaker, script, char_cfg)
            if not voice:
                fallback_voices = {"A": "zh-CN-XiaoyiNeural", "B": "zh-CN-YunxiNeural"}
                voice = fallback_voices.get(speaker, fallback_voices["A"])
                print("  warning: no character name for speaker %s, using fallback voice" % speaker)
            spk_rate = spk_rate or rate

            raw_text = line["text"]
            spoken_text, sfx_key = split_sfx(raw_text)
            if spoken_text:
                tts_text = preprocess_text(spoken_text, char_name)
            else:
                tts_text = " "

            spk_token = sanitize_slug(speaker, fallback="A")
            fname = "tts_%s_line%04d_%s.mp3" % (slug, idx + 1, spk_token)
            fpath = os.path.join(out_dir, fname)

            # --- Cache lookup ---
            ckey = cache_key(tts_text, voice, spk_rate)
            cache_mp3 = os.path.join(cache_dir, ckey + ".mp3")
            cache_meta = os.path.join(cache_dir, ckey + ".json")

            if use_cache and os.path.isfile(cache_mp3) and os.path.isfile(cache_meta):
                # Verify cache metadata matches
                with open(cache_meta, "r", encoding="utf-8") as cf:
                    meta = json.load(cf)
                if (meta.get("text") == tts_text and
                    meta.get("voice") == voice and
                    meta.get("rate") == (spk_rate or "")):
                    shutil.copy2(cache_mp3, fpath)
                    cache_hits += 1
                    print("  line %d: cache hit" % (idx + 1))
                    # Mark SFX if needed
                    if sfx_key:
                        line["_sfx"] = sfx_key
                    else:
                        line.pop("_sfx", None)
                    line["audio_file"] = "audio/lines/" + fname
                    return fpath

            # --- Cache miss: generate TTS ---
            cache_misses += 1
            for attempt in range(1, retries + 1):
                try:
                    await edge_tts.Communicate(
                        tts_text, voice, rate=spk_rate, proxy=proxy
                    ).save(fpath)
                    break
                except Exception as e:
                    if attempt == retries:
                        raise
                    print("  retry %d/%d (line %d): %s" % (attempt, retries, idx + 1, e))
                    await asyncio.sleep(1.0 * attempt)

            # Save to cache
            if use_cache:
                shutil.copy2(fpath, cache_mp3)
                with open(cache_meta, "w", encoding="utf-8") as cf:
                    json.dump({"text": tts_text, "voice": voice, "rate": spk_rate or ""}, cf,
                              ensure_ascii=False, indent=2)

            if sfx_key:
                line["_sfx"] = sfx_key
                print("  line %d: SFX '%s' will be appended at compose time" % (idx + 1, sfx_key))
            else:
                line.pop("_sfx", None)

            line["audio_file"] = "audio/lines/" + fname
            return fpath

    tasks = [one(i, ln) for i, ln in enumerate(script["lines"])]
    paths = await asyncio.gather(*tasks)

    if use_cache:
        print("TTS cache: %d hits, %d misses" % (cache_hits, cache_misses))
    return paths


def main():
    ap = argparse.ArgumentParser(description="script JSON -> per-line TTS + retimed JSON")
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", default=None, help="output JSON (default: overwrite input)")
    ap.add_argument("--assets-root", default="assets")
    ap.add_argument("--rate", default="+12%", help="edge-tts rate fallback, e.g. +12%%")
    ap.add_argument("--proxy", default=None,
                    help="proxy URL for edge-tts, e.g. http://127.0.0.1:7890 or socks5://127.0.0.1:1080")
    ap.add_argument("--no-cache", action="store_true", help="disable TTS audio cache, force regenerate all")
    args = ap.parse_args()

    with open(args.script, "r", encoding="utf-8-sig") as fh:
        script = json.load(fh)
    char_cfg = load_characters(args.assets_root)
    slug = sanitize_slug(script.get("id", "video"))
    lines_dir = os.path.join(args.assets_root, "audio", "lines")
    os.makedirs(lines_dir, exist_ok=True)

    paths = asyncio.run(synth_all(script, lines_dir, args.rate, slug, char_cfg,
                                  args.assets_root, args.proxy,
                                  use_cache=not args.no_cache))
    cursor = LEAD
    for ln, p in zip(script["lines"], paths):
        dur = probe_duration(p)
        ln["start"] = round(cursor, 3)
        ln["end"] = round(cursor + dur, 3)
        cursor = ln["end"] + GAP

    out = args.out or args.script
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(script, fh, ensure_ascii=False, indent=2)
    print("wrote %s: %d lines, %.1fs total" % (out, len(script["lines"]), cursor))


if __name__ == "__main__":
    main()
