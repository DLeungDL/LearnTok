#!/usr/bin/env python3
"""calibrate_audio.py — loudness calibration for LearnTok AI audio assets.

Measures EBU R128 integrated loudness (via ffmpeg ebur128) and writes back:

1. Voice balance: per-character `voice_gain_db` in assets/characters.json so all
   characters in the given script land on --voice-target LUFS.
2. BGM balance: per-track `volume` in assets/manifest.json so every track lands
   on --bgm-target LUFS (or matches the --bgm-standard track, which keeps its
   current manifest volume and becomes the reference).

Usage:
  python -m learntok.tools.calibrate_audio --script pipeline/examples/script_xxx.json
  python -m learntok.tools.calibrate_audio --script <json> --dry-run          # preview only
  python -m learntok.tools.calibrate_audio --script <json> --voice-only
  python -m learntok.tools.calibrate_audio --bgm-only --bgm-standard bgm/bgm_sacred_play_secret_place.mp3

Requires: ffmpeg on PATH or pipeline/tools/ffmpeg/ffmpeg.exe.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

from learntok import config
from learntok.tools import model_safety


def find_ffmpeg():
    return config.find_tool("ffmpeg")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_json(path, data):
    with open(path, "w", encoding="utf-8-sig") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def measure_lufs(ffmpeg, path):
    """Return integrated loudness (LUFS) or None on failure."""
    cmd = [ffmpeg, "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    m = re.search(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", proc.stderr, re.MULTILINE)
    return float(m.group(1)) if m else None


def energy_mean(lufs_list):
    """Average LUFS values by energy (proper loudness mean)."""
    if not lufs_list:
        return None
    return 10 * math.log10(sum(10 ** (l / 10.0) for l in lufs_list) / len(lufs_list))


def calibrate_voice(ffmpeg, assets_root, script_path, target, dry_run):
    """Measure per-character loudness from a script's line audio files."""
    script = load_json(script_path)
    char_map = {key: ch.get("name", "") for key, ch in script.get("characters", {}).items()}
    lines = script.get("lines", [])
    timed = [ln for ln in lines if ln.get("audio_file")]
    if not timed:
        print("voice: no audio_file fields in script (run tts_edge.py first); skipping")
        return {}, {}
    measured = {}
    for sp, name in char_map.items():
        files = [ln["audio_file"] for ln in timed if ln.get("speaker") == sp]
        if not files:
            continue
        lufs = []
        for rel in files:
            try:
                p = model_safety.resolve_contained(assets_root, rel, "")
            except model_safety.ModelSafetyError as e:
                print("  warning: %s" % e)
                continue
            if not os.path.isfile(p):
                print("  warning: missing %s" % rel)
                continue
            v = measure_lufs(ffmpeg, p)
            if v is not None:
                lufs.append(v)
        avg = energy_mean(lufs)
        if avg is None:
            continue
        gain = round(target - avg, 1)
        measured[name] = {"speaker": sp, "lines": len(lufs), "lufs": avg, "gain": gain}
        print("voice  %-8s %-8s lines=%-3d measured=%6.1f LUFS  gain=%+5.1f dB -> %6.1f LUFS"
              % (sp, name, len(lufs), avg, gain, target))
    return measured, {name: v["gain"] for name, v in measured.items()}


def calibrate_bgm(ffmpeg, assets_root, manifest, target, standard_rel, dry_run):
    """Measure each manifest BGM track; compute volume to hit target loudness."""
    bgm = manifest.get("bgm", [])
    if not bgm:
        print("bgm: no bgm entries in manifest; skipping")
        return {}
    std_lufs = std_vol = None
    if standard_rel:
        try:
            std_path = model_safety.resolve_contained(assets_root, standard_rel, "")
        except model_safety.ModelSafetyError as e:
            print("bgm: %s; ignoring --bgm-standard" % e)
            std_path = None
        std_lufs = measure_lufs(ffmpeg, std_path) if std_path else None
        std_entry = next((b for b in bgm if b.get("file") == standard_rel), None)
        std_vol = float(std_entry.get("volume", 0.08)) if std_entry else None
        if std_lufs is None or std_vol is None:
            print("bgm: standard track '%s' not measurable or not in manifest; ignoring"
                  % standard_rel)
            std_lufs = std_vol = None
    if std_lufs is not None:
        target = std_lufs + 20 * math.log10(std_vol)
        print("bgm: standard %s @ vol %.3f -> effective %.1f LUFS" % (standard_rel, std_vol, target))
    else:
        print("bgm: target %.1f LUFS" % target)
    volumes = {}
    for b in bgm:
        rel = b.get("file", "")
        if not rel:
            continue
        try:
            p = model_safety.resolve_contained(assets_root, rel, "")
        except model_safety.ModelSafetyError as e:
            print("  warning: %s" % e)
            continue
        if not os.path.isfile(p):
            print("  warning: missing %s" % rel)
            continue
        lufs = measure_lufs(ffmpeg, p)
        if lufs is None:
            continue
        vol = round(10 ** ((target - lufs) / 20.0), 3)
        volumes[rel] = vol
        print("bgm   %-40s measured=%6.1f LUFS  volume=%.3f -> %6.1f LUFS"
              % (os.path.basename(rel), lufs, vol, target))
    return volumes


def main():
    ap = argparse.ArgumentParser(description="Calibrate character voice and BGM loudness.")
    ap.add_argument("--script", help="script JSON (needed for voice calibration)")
    ap.add_argument("--assets-root", default=config.assets_root())
    ap.add_argument("--manifest", default=os.path.join(config.assets_root(), "manifest.json"))
    ap.add_argument("--characters", default=os.path.join(config.assets_root(), "characters.json"))
    ap.add_argument("--voice-target", type=float, default=-19.6, help="target voice LUFS (default -19.6)")
    ap.add_argument("--bgm-target", type=float, default=-24.0, help="target BGM LUFS (default -24.0)")
    ap.add_argument("--bgm-standard", default="", help="reference BGM file (keeps its volume); overrides --bgm-target")
    ap.add_argument("--voice-only", action="store_true")
    ap.add_argument("--bgm-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print results without writing files")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    manifest = load_json(args.manifest) if os.path.isfile(args.manifest) else {}
    characters = load_json(args.characters) if os.path.isfile(args.characters) else {}

    voice_gains = {}
    if args.script and not args.bgm_only:
        _, voice_gains = calibrate_voice(ffmpeg, args.assets_root, args.script, args.voice_target, args.dry_run)

    bgm_vols = {}
    if not args.voice_only:
        bgm_vols = calibrate_bgm(ffmpeg, args.assets_root, manifest, args.bgm_target, args.bgm_standard, args.dry_run)

    if args.dry_run:
        print("dry-run: no files written")
        return

    if bgm_vols and not args.voice_only:
        for b in manifest.get("bgm", []):
            rel = b.get("file", "")
            if rel in bgm_vols:
                b["volume"] = bgm_vols[rel]
        save_json(args.manifest, manifest)
        print("bgm: wrote %d volume(s) to %s" % (len(bgm_vols), args.manifest))

    if voice_gains and not args.bgm_only:
        for name, gain in voice_gains.items():
            characters.setdefault(name, {})["voice_gain_db"] = gain
        save_json(args.characters, characters)
        print("voice: wrote %d gain(s) to %s" % (len(voice_gains), args.characters))


if __name__ == "__main__":
    main()
