"""Patched utils.py — uses real fairseq with Python 3.12 compatibility shim.

Patches dataclasses._get_field to allow mutable default values (removed in Python 3.12),
so fairseq can import and load the original HuBERT checkpoint correctly.
"""
import hashlib
import json
import os
import sys
import types
import dataclasses as _dc

# === Python 3.12 compatibility shim ===
_orig_get_field = _dc._get_field

def _patched_get_field(cls, name, type, kw_only):
    try:
        return _orig_get_field(cls, name, type, kw_only)
    except ValueError as e:
        if "mutable default" not in str(e):
            raise
        default = getattr(cls, name)
        f = _dc.field(default_factory=lambda d=default: d)
        f.name = name
        f.type = type
        f.default = default
        return f

_dc._get_field = _patched_get_field

# hydra.experimental stub (moved in hydra-core 1.1+)
if "hydra.experimental" not in sys.modules:
    try:
        import hydra.experimental  # noqa
    except ImportError:
        _mod = types.ModuleType("hydra.experimental")
        _mod.compose = lambda *a, **k: None
        _mod.initialize = lambda *a, **k: None
        sys.modules["hydra.experimental"] = _mod
# === End shim ===

# === Base-model integrity pin (must match assets/rvc_models/manifest.json) ===
# load_hubert feeds hubert_base.pt to fairseq -> torch.load (pickle), so the
# SHA-256 is verified before loading. This file is copied into site-packages by
# scripts/setup.ps1 (vendored); after upgrading rvc-python, re-pin the hash in
# BOTH places and re-run setup.ps1.
_BASE_MODEL_PINS = {
    "hubert_base.pt": {
        "sha256": "f54b40fd2802423a5643779c4861af1e9ee9c1564dc9d32f54f20b5ffba7db96",
        "size": 189507909,
    },
}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_base_entry(name):
    """Prefer the repo manifest (via LEARNTOK_ASSETS_ROOT) over the embedded pin."""
    root = os.environ.get("LEARNTOK_ASSETS_ROOT")
    if root:
        try:
            with open(os.path.join(root, "rvc_models", "manifest.json"), encoding="utf-8") as fh:
                return json.load(fh).get("base_models", {}).get(name)
        except Exception:
            pass
    return _BASE_MODEL_PINS.get(name)


def _verify_base_model(path, name):
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        raise FileNotFoundError("base model not found: %s" % real)
    entry = _manifest_base_entry(name)
    if not entry or not entry.get("sha256"):
        print("WARNING: no integrity pin for %s — loading unverified" % name, file=sys.stderr)
        return real
    actual = _sha256_file(real)
    if actual != entry["sha256"].lower():
        raise RuntimeError(
            "integrity check failed for %s: SHA-256 %s != pinned %s "
            "(tampered file or stale pin; re-run scripts/setup.ps1)"
            % (name, actual, entry["sha256"])
        )
    expected_size = entry.get("size")
    if expected_size and os.path.getsize(real) != expected_size:
        raise RuntimeError(
            "size mismatch for %s: expected %d, got %d"
            % (name, expected_size, os.path.getsize(real))
        )
    return real


from fairseq import checkpoint_utils


def get_index_path_from_model(sid):
    return next(
        (
            f
            for f in [
                os.path.join(root, name)
                for root, _, files in os.walk(os.getenv("index_root"), topdown=False)
                for name in files
                if name.endswith(".index") and "trained" not in name
            ]
            if sid.split(".")[0] in f
        ),
        "",
    )


def load_hubert(config, lib_dir):
    model_dir = os.path.realpath(os.path.join(lib_dir, "base_model"))
    model_path = _verify_base_model(os.path.join(model_dir, "hubert_base.pt"), "hubert_base.pt")
    if os.path.dirname(model_path) != model_dir:
        raise RuntimeError("base model escapes %s" % model_dir)
    models, _, _ = checkpoint_utils.load_model_ensemble_and_task(
        [model_path],
        suffix="",
    )
    hubert_model = models[0]
    hubert_model = hubert_model.to(config.device)
    if config.is_half:
        hubert_model = hubert_model.half()
    else:
        hubert_model = hubert_model.float()
    return hubert_model.eval()
