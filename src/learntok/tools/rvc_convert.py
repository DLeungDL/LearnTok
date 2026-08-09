"""RVC voice conversion — converts TTS audio lines to character voices.

Usage:
  python -m learntok.tools.rvc_convert --script pipeline/examples/script_public_vs_private.json
  python -m learntok.tools.rvc_convert --script <json> --speaker A --pitch 0

Requires: torch (CUDA), rvc-python, ffmpeg/ffprobe (bundled).
Model files in assets/rvc_models/rvc_<name>_v<ver>.pth [+ .index]

RVC config is read from assets/characters.json by character name.
Characters with rvc_model=null are skipped (TTS-only).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from learntok import config
from learntok.tools import model_safety

def load_characters(assets_root):
    """Load character config from assets/characters.json."""
    path = os.path.join(assets_root, "characters.json")
    if not os.path.isfile(path):
        sys.exit("error: %s not found" % path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def resolve_rvc_config(speaker_key, script, char_cfg):
    """Resolve RVC config from script JSON character name -> characters.json.
    Returns None if character has no RVC model."""
    name = script.get("characters", {}).get(speaker_key, {}).get("name", "")
    if name and name in char_cfg:
        cfg = char_cfg[name]
        if not cfg.get("rvc_model"):
            return None  # No RVC model for this character
        # Carry over the full character entry so optional RVC tuning params
        # (protect / resample_sr / filter_radius / rms_mix_rate) reach
        # convert_line instead of being silently dropped. model/index/pitch
        # aliases keep the existing accessors working.
        resolved = dict(cfg)
        resolved["model"] = cfg["rvc_model"]
        resolved["index"] = cfg.get("rvc_index")
        resolved["pitch"] = cfg.get("pitch", 0)
        resolved["f0method"] = cfg.get("f0method", "rmvpe")
        resolved["index_rate"] = cfg.get("index_rate", 0.5)
        return resolved
    return None


def find_tool(name):
    return config.find_tool(name)


def mp3_to_wav(ffmpeg, mp3_path, wav_path):
    subprocess.run([ffmpeg, "-y", "-i", mp3_path, "-ar", "44100", "-ac", "1", wav_path],
                   capture_output=True, check=True)


def wav_to_mp3(ffmpeg, wav_path, mp3_path, bitrate="192k"):
    subprocess.run([ffmpeg, "-y", "-i", wav_path, "-b:a", bitrate, mp3_path],
                   capture_output=True, check=True)


def convert_line(rvc, ffmpeg, in_mp3, out_mp3, cfg, tmp_dir):
    """MP3 -> WAV -> RVC -> WAV -> MP3"""
    base = os.path.splitext(os.path.basename(in_mp3))[0]
    wav_in = os.path.join(tmp_dir, base + "_in.wav")
    wav_out = os.path.join(tmp_dir, base + "_out.wav")
    mp3_to_wav(ffmpeg, in_mp3, wav_in)
    params = dict(
        f0up_key=cfg["pitch"],
        f0method=cfg["f0method"],
        index_rate=cfg["index_rate"],
    )
    for opt_key in ("protect", "resample_sr", "filter_radius", "rms_mix_rate"):
        if opt_key in cfg:
            params[opt_key] = cfg[opt_key]
    rvc.set_params(**params)
    rvc.infer_file(wav_in, wav_out)
    wav_to_mp3(ffmpeg, wav_out, out_mp3)


def main():
    ap = argparse.ArgumentParser(description="RVC voice conversion for TTS lines")
    ap.add_argument("--script", required=True, help="script JSON path")
    ap.add_argument("--assets-root", default="assets")
    ap.add_argument("--speaker", default=None, help="only convert this speaker key (default: all)")
    ap.add_argument("--pitch", type=int, default=None, help="override pitch shift (semitones)")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="load RVC files missing from manifest.json (with a loud warning)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be converted")
    args = ap.parse_args()

    assets_root = os.path.abspath(args.assets_root)
    # Consumed by the vendored utils_patched.load_hubert (copied into
    # site-packages by setup.ps1) to verify hubert_base.pt against the manifest.
    os.environ["LEARNTOK_ASSETS_ROOT"] = assets_root

    with open(args.script, "r", encoding="utf-8-sig") as fh:
        script = json.load(fh)

    char_cfg = load_characters(assets_root)
    manifest = model_safety.load_manifest(assets_root)
    ffmpeg = find_tool("ffmpeg")

    # Build active model map: speaker_key -> config
    active = {}
    for spk_key in script.get("characters", {}):
        if args.speaker and spk_key != args.speaker:
            continue
        cfg = resolve_rvc_config(spk_key, script, char_cfg)
        if cfg is None:
            name = script["characters"][spk_key].get("name", spk_key)
            print("skip: speaker %s (%s) has no RVC model" % (spk_key, name))
            continue
        try:
            pth = model_safety.verify_rvc_file(assets_root, cfg["model"], manifest,
                                               allow_unverified=args.allow_unverified)
        except model_safety.ModelSafetyError as e:
            print("error: model for speaker %s (%s): %s" % (spk_key, cfg["model"], e))
            continue
        idx = None
        if cfg.get("index"):
            try:
                idx = model_safety.verify_rvc_file(assets_root, cfg["index"], manifest,
                                                   allow_unverified=args.allow_unverified)
            except model_safety.ModelSafetyError as e:
                print("error: index for speaker %s (%s): %s" % (spk_key, cfg["index"], e))
                continue
        pitch = args.pitch if args.pitch is not None else cfg["pitch"]
        active[spk_key] = {**cfg, "pth": pth, "idx": idx, "pitch": pitch}

    if not active:
        print("no RVC models available. Nothing to convert.")
        return

    # Collect lines to convert
    to_convert = []
    for i, ln in enumerate(script["lines"]):
        spk = ln.get("speaker", "")
        if spk not in active:
            continue
        audio = ln.get("audio_file")
        if not audio:
            continue
        try:
            mp3_path = model_safety.resolve_contained(assets_root, audio, "")
        except model_safety.ModelSafetyError as e:
            print("warning: bad audio path for line %d: %s" % (i + 1, e))
            continue
        if not os.path.isfile(mp3_path):
            print("warning: audio missing: %s" % mp3_path)
            continue
        to_convert.append((i, spk, mp3_path))

    print("RVC conversion: %d lines (%s)" % (len(to_convert), ", ".join(active.keys())))
    if args.dry_run:
        for i, spk, p in to_convert[:10]:
            print("  line %d [%s] %s" % (i + 1, spk, os.path.basename(p)))
        if len(to_convert) > 10:
            print("  ... and %d more" % (len(to_convert) - 10))
        return

    # Load RVC models
    from rvc_python.infer import RVCInference
    rvc_instances = {}
    for spk, cfg in active.items():
        print("loading model for speaker %s: %s" % (spk, cfg["model"]))
        rvc = RVCInference(model_path=cfg["pth"], index_path=cfg["idx"], device="cuda:0", version="v2")
        rvc_instances[spk] = rvc

    tmp_dir = tempfile.mkdtemp(prefix="rvc_")
    converted = 0
    for idx, spk, mp3_path in to_convert:
        rvc = rvc_instances[spk]
        cfg = active[spk]
        try:
            convert_line(rvc, ffmpeg, mp3_path, mp3_path, cfg, tmp_dir)
            converted += 1
            if converted % 10 == 0:
                print("  converted %d/%d ..." % (converted, len(to_convert)))
        except Exception as e:
            print("  ERROR line %d: %s" % (idx + 1, e))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("done: %d/%d lines converted" % (converted, len(to_convert)))



if __name__ == "__main__":
    main()
