"""learntok.cli — console entry point (``learntok``).

Each subcommand dispatches to the matching tool module via
``python -m learntok.<module>`` so the per-tool argparse behaviour (help,
defaults, exit codes) stays exactly as before the packaging migration.
"""
import os
import subprocess
import sys

SUBMODULES = {
    "make": "learntok.tools.run_pipeline",
    "script-gen": "learntok.tools.script_gen",
    "tts": "learntok.tools.tts_edge",
    "rvc": "learntok.tools.rvc_convert",
    "calibrate": "learntok.tools.calibrate_audio",
    "compose": "learntok.compose",
    "validate": "learntok.tools.validate_script",
    "fix": "learntok.tools.script_fix",
    "ingest-srt": "learntok.tools.ingest_srt",
    "migrate-terms": "learntok.tools.migrate_terms",
    "rag-build": "learntok.tools.rag_build",
    "rag-retrieve": "learntok.tools.rag_retrieve",
    "doctor": "learntok.doctor",
}

HELP = """usage: learntok <subcommand> [args...]

LearnTok AI pipeline CLI. Subcommands:

  make           run the full pipeline (TTS -> RVC -> calibrate -> compose)
  script-gen     LLM script generation from source material
  tts            Edge-TTS voice generation + timeline backfill
  rvc            RVC voice conversion (requires NVIDIA GPU)
  calibrate      loudness calibration for characters / BGM
  compose        ffmpeg compositing (subtitle burn + mix + render)
  validate       validate a script JSON (quality gate)
  fix            deterministic script post-processing
  ingest-srt     convert an SRT file into a script JSON
  migrate-terms  migrate inline-parenthesized terms to structured terms
  rag-build      build the ChromaDB knowledge base
  rag-retrieve   query the ChromaDB knowledge base
  doctor         environment checks
  init [dir]     create a workspace skeleton in dir (default: current dir)

Run 'learntok <subcommand> --help' for per-tool options.
"""


def module_for(subcommand):
    return SUBMODULES[subcommand]


def build_command(subcommand, args):
    return [sys.executable, "-m", module_for(subcommand)] + list(args)


def init_workspace(target):
    """Create a minimal workspace skeleton (output / build dirs with .gitkeep)."""
    target = os.path.abspath(target)
    for rel in ("output", "pipeline/build", "assets/audio/cache",
                "assets/audio/lines", "assets/rag"):
        d = os.path.join(target, rel)
        os.makedirs(d, exist_ok=True)
        keep = os.path.join(d, ".gitkeep")
        if not os.path.exists(keep):
            with open(keep, "w", encoding="utf-8") as fh:
                fh.write("")
    print("workspace skeleton created at %s" % target)
    print("next: copy assets/characters.json + assets/manifest.json, then run 'learntok doctor'")
    return target


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(HELP)
        return 0
    sub = args[0]
    if sub == "init":
        rest = args[1:]
        target = rest[0] if rest and not rest[0].startswith("-") else "."
        init_workspace(target)
        return 0
    if sub not in SUBMODULES:
        print("error: unknown subcommand '%s'" % sub, file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 2
    cmd = build_command(sub, args[1:])
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())