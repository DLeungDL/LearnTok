#!/usr/bin/env python3
"""run_pipeline.py — one-command LearnTok AI audio + video pipeline.

Chains the existing tools in order:
  tts_edge.py -> (rvc_convert.py) -> calibrate_audio.py -> compose.py

Loudness calibration runs automatically after voice generation so character
voice gains and BGM volumes stay aligned; skip it with --skip-calibrate.

Usage:
  python -m learntok.tools.run_pipeline --script pipeline/examples/script_xxx.json --seed 42
  python -m learntok.tools.run_pipeline --script <json> --skip-rvc         # TTS only voice
  python -m learntok.tools.run_pipeline --script <json> --skip-calibrate   # no recalibration
  python -m learntok.tools.run_pipeline --script <json> --skip-tts         # reuse existing TTS audio
  python -m learntok.tools.run_pipeline --script <json> --dry-run          # preview all commands
"""
import argparse
import os
import subprocess
import sys

from learntok import config

PYTHON = sys.executable


def tool(module):
    """Return [python, -m, learntok.tools.<module>] prefix for a tool module."""
    return ["-m", "learntok.tools.%s" % module]


def run_step(label, argv, dry_run):
    cmd = [PYTHON]
    for part in argv:
        cmd.extend(part) if isinstance(part, list) else cmd.append(part)
    print("\n===== [%s] =====" % label)
    if dry_run:
        print("[dry-run] " + " ".join(cmd))
        return
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        sys.exit("error: step [%s] failed (exit %d)" % (label, proc.returncode))


def main():
    ap = argparse.ArgumentParser(description="Run the LearnTok AI pipeline end-to-end.")
    ap.add_argument("--script", required=True, help="script JSON (same JSON flows through every step)")
    ap.add_argument("--seed", type=int, default=None, help="compose 背景/BGM 種子（預設 None＝每次隨機）")
    ap.add_argument("--skip-tts", action="store_true", help="reuse existing TTS audio, skip edge-tts")
    ap.add_argument("--skip-rvc", action="store_true", help="skip RVC voice conversion")
    ap.add_argument("--skip-calibrate", action="store_true", help="skip loudness calibration")
    ap.add_argument("--max-duration", type=float, default=0, help="pass through to compose (0 = unlimited)")
    ap.add_argument("--dry-run", action="store_true", help="print every step command without running")
    args = ap.parse_args()

    script = args.script
    if not args.skip_tts:
        run_step("TTS (edge-tts)", [tool("tts_edge")] + ["--script", script], args.dry_run)
    if not args.skip_rvc:
        run_step("RVC (voice conversion)", [tool("rvc_convert")] + ["--script", script], args.dry_run)
    if not args.skip_calibrate:
        run_step("LOUDNESS CALIBRATION", [tool("calibrate_audio")] + ["--script", script], args.dry_run)

    compose_args = ["-m", "learntok.compose", "--script", script]
    if args.seed is not None:
        compose_args += ["--seed", str(args.seed)]
    if args.max_duration > 0:
        compose_args += ["--max-duration", str(args.max_duration)]
    run_step("COMPOSE (render)", compose_args, args.dry_run)

    if args.dry_run:
        print("\ndry-run: pipeline preview complete (no files changed)")


if __name__ == "__main__":
    main()