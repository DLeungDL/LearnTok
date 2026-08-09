"""Model / asset integrity helpers for the RVC pipeline.

Every model, index, and base-model file that reaches torch.load (via
rvc_python or fairseq) is verified against assets/rvc_models/manifest.json
before loading:

  * containment — the resolved realpath must stay inside the intended dir;
  * provenance — the filename must be listed in the manifest;
  * integrity — SHA-256 (and size) must match the pinned value.

Loading itself is delegated to rvc-python 0.1.5 / fairseq, which call
torch.load(...) without weights_only=True (third-party code), so the manifest
check is the enforced boundary. Keep rvc-python and fairseq versions pinned
(see requirements.txt) and re-pin hashes whenever they change.
"""
import hashlib
import json
import os
import sys

MODELS_SUBDIR = "rvc_models"
MANIFEST_NAME = "manifest.json"


class ModelSafetyError(RuntimeError):
    """Raised when a model/asset fails integrity or containment checks."""


def sha256_file(path, chunk_size=1024 * 1024):
    """Return the lowercase hex SHA-256 of a file (chunked, memory-safe)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(assets_root):
    """Load assets/rvc_models/manifest.json -> {models: {...}, base_models: {...}}."""
    path = os.path.join(assets_root, MODELS_SUBDIR, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise ModelSafetyError("integrity manifest not found: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {
        "models": data.get("models") or {},
        "base_models": data.get("base_models") or {},
    }


def resolve_contained(root, rel_path, subdir=""):
    """Return the realpath of root/[subdir/]rel_path, refusing any escape.

    rel_path may be a bare filename (RVC models) or a repo-relative asset path
    (audio/manifest fields). Raises ModelSafetyError when the resolved path
    leaves the intended directory (absolute paths, '..', symlinks, ...).
    """
    base = os.path.realpath(os.path.join(root, subdir)) if subdir else os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel_path))
    if full == base:
        raise ModelSafetyError("resolved path is the directory itself: %s" % rel_path)
    if not full.startswith(base + os.sep):
        raise ModelSafetyError("path escapes %r: %s" % (base, rel_path))
    return full


def _check_entry(name, entry, real, allow_unverified):
    """Enforce provenance (listed) and integrity (SHA-256/size) for one file."""
    if not entry:
        if allow_unverified:
            print(
                "WARNING: %s is not listed in %s — loading UNVERIFIED"
                % (name, os.path.join(MODELS_SUBDIR, MANIFEST_NAME)),
                file=sys.stderr,
            )
            return
        raise ModelSafetyError(
            "%s is not listed in %s/%s — refusing to load unlisted model "
            "(pass --allow-unverified to override)"
            % (name, MODELS_SUBDIR, MANIFEST_NAME)
        )
    expected_sha = (entry.get("sha256") or "").lower()
    if expected_sha and sha256_file(real) != expected_sha:
        raise ModelSafetyError(
            "SHA-256 mismatch for %s: file was tampered or manifest is stale" % name
        )
    expected_size = entry.get("size")
    if expected_size and os.path.getsize(real) != expected_size:
        raise ModelSafetyError(
            "size mismatch for %s: expected %d, got %d"
            % (name, expected_size, os.path.getsize(real))
        )


def verify_rvc_file(assets_root, rel_path, manifest, allow_unverified=False):
    """Verify an RVC model/index file and return its contained realpath."""
    real = resolve_contained(assets_root, rel_path, MODELS_SUBDIR)
    if not os.path.isfile(real):
        raise ModelSafetyError("model file not found: %s" % real)
    _check_entry(os.path.basename(real), manifest["models"].get(os.path.basename(real)),
                 real, allow_unverified)
    return real


def verify_base_model(base_dir, name, manifest, allow_unverified=False):
    """Verify a base model (e.g. hubert_base.pt) inside base_dir."""
    real = resolve_contained(base_dir, name, "")
    if not os.path.isfile(real):
        raise ModelSafetyError("base model not found: %s" % real)
    _check_entry(name, manifest["base_models"].get(name), real, allow_unverified)
    return real
