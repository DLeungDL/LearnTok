"""learntok.config — single workspace resolution point.

Resolution order for the workspace root:
1. ``LEARNTOK_WORKSPACE`` environment variable
2. Optional ``learntok.toml`` in the current directory: ``[workspace] root = "..."``
3. Walk up from the start directory looking for a workspace marker
   (a directory containing ``assets/characters.json``)
4. The start directory itself

Every tool resolves ``assets/``, ``pipeline/build/`` and the bundled ffmpeg
through this module so the packaged code no longer hard-codes ``REPO_ROOT``.
"""
import os
import shutil
import sys

ENV_WORKSPACE = "LEARNTOK_WORKSPACE"
ENV_ASSETS = "LEARNTOK_ASSETS_ROOT"
ENV_FFMPEG_DIR = "LEARNTOK_FFMPEG_DIR"
WORKSPACE_MARKER = os.path.join("assets", "characters.json")
CONFIG_FILE = "learntok.toml"


def _walk_up(start):
    current = os.path.abspath(start)
    while True:
        yield current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _config_file_root(start):
    """Return workspace root declared in learntok.toml, or None."""
    for d in _walk_up(start):
        cfg = os.path.join(d, CONFIG_FILE)
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                in_workspace = False
                for raw in fh:
                    line = raw.strip()
                    if line.startswith("[") and line.endswith("]"):
                        in_workspace = line.lower() == "[workspace]"
                        continue
                    if in_workspace and line.lower().startswith("root"):
                        value = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if value:
                            return os.path.abspath(os.path.join(d, value))
        except OSError:
            pass
    return None


def workspace_root(start=None):
    """Resolve the LearnTok workspace root (see module docstring)."""
    start = start or os.getcwd()
    env = os.environ.get(ENV_WORKSPACE, "").strip()
    if env:
        return os.path.abspath(env)
    cfg_root = _config_file_root(start)
    if cfg_root:
        return cfg_root
    for d in _walk_up(start):
        if os.path.isfile(os.path.join(d, WORKSPACE_MARKER)):
            return d
    return os.path.abspath(start)


def assets_root():
    """Resolve the assets directory (env override keeps old LEARNTOK_ASSETS_ROOT working)."""
    env = os.environ.get(ENV_ASSETS, "").strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(workspace_root(), "assets")


def build_dir():
    """Resolve the pipeline build (intermediate artifacts) directory."""
    return os.path.join(workspace_root(), "pipeline", "build")


def ffmpeg_dir():
    """Return the bundled ffmpeg directory (empty string when unavailable)."""
    env = os.environ.get(ENV_FFMPEG_DIR, "").strip()
    if env:
        return os.path.abspath(env)
    bundled = os.path.join(workspace_root(), "pipeline", "tools", "ffmpeg")
    if os.path.isdir(bundled):
        return bundled
    return ""


def find_tool(name, exit_on_missing=True):
    """Locate an ffmpeg-family tool: bundled dir first, then PATH.

    Returns None (instead of exiting) when ``exit_on_missing`` is False,
    which is what ``learntok doctor`` uses for a non-fatal probe.
    """
    d = ffmpeg_dir()
    if d:
        candidate = os.path.join(d, name + ".exe")
        if os.path.isfile(candidate):
            return candidate
    exe = shutil.which(name)
    if exe:
        return exe
    if exit_on_missing:
        sys.exit("error: '%s' not found (checked pipeline/tools/ffmpeg and PATH). Install ffmpeg first." % name)
    return None


def load_env():
    """Load .env files (env vars already set take precedence).

    Checks ``pipeline/tools/.env`` (legacy location) then ``.env`` in the
    workspace root; both are gitignored so API keys never reach git.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (
        os.path.join(workspace_root(), "pipeline", "tools", ".env"),
        os.path.join(workspace_root(), ".env"),
    ):
        if os.path.isfile(path):
            load_dotenv(path)