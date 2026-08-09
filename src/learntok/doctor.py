"""learntok.doctor — deterministic environment checks (``learntok doctor``).

Every check is machine-decodable: prints ``[OK] / [WARN] / [FAIL]`` per item
plus a one-line summary. Exit code 0 when nothing failed, 1 otherwise.
"""
import json
import os
import sys

from learntok import config


def _result(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def check_python():
    ok = sys.version_info >= (3, 10)
    return _result("python", "OK" if ok else "FAIL", "%d.%d.%d" % sys.version_info[:3])


def check_package():
    try:
        import learntok
    except Exception as exc:
        return _result("package", "FAIL", str(exc)[:200])
    return _result("package", "OK", "learntok %s" % getattr(learntok, "__version__", "?"))


def check_torch():
    try:
        import torch
    except ImportError:
        return _result("torch", "WARN", "未安裝（RVC 需要 pip install -e .[rvc]）")
    if torch.cuda.is_available():
        return _result("torch", "OK", "CUDA %s" % (torch.version.cuda or "available"))
    return _result("torch", "WARN", "已安裝但無 CUDA（RVC 需要 NVIDIA GPU）")


def check_fairseq():
    try:
        import fairseq  # noqa: F401
    except Exception as exc:
        return _result("fairseq", "WARN", "無法 import（%s；僅 RVC 需要）" % type(exc).__name__)
    return _result("fairseq", "OK", "import 成功")


def check_ffmpeg():
    ffmpeg = config.find_tool("ffmpeg", exit_on_missing=False)
    if not ffmpeg:
        return _result("ffmpeg", "FAIL", "找不到（pipeline/tools/ffmpeg 或 PATH）")
    return _result("ffmpeg", "OK", os.path.basename(ffmpeg))


def check_api_key():
    config.load_env()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return _result("api-key", "OK", "已設定（值不顯示）")
    return _result("api-key", "WARN", "DEEPSEEK_API_KEY 未設定（僅 LLM 腳本生成需要）")


def _load_json(rel_path):
    path = os.path.join(config.assets_root(), rel_path)
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def check_characters():
    try:
        data = _load_json("characters.json")
    except Exception as exc:
        return _result("characters", "FAIL", str(exc)[:200])
    if not isinstance(data, dict) or not data:
        return _result("characters", "FAIL", "空的或非 map")
    return _result("characters", "OK", "%d 個角色" % len(data))


def check_manifest():
    try:
        data = _load_json("manifest.json")
    except Exception as exc:
        return _result("manifest", "FAIL", str(exc)[:200])
    if not isinstance(data, dict):
        return _result("manifest", "FAIL", "非 JSON object")
    return _result("manifest", "OK", "keys: %s" % ", ".join(sorted(data)))


def check_rvc_models():
    try:
        from learntok.tools import model_safety
        manifest = model_safety.load_manifest(config.assets_root())
    except Exception as exc:
        return _result("rvc-models", "FAIL", str(exc)[:200])
    entries = manifest.get("models") or {}
    if not entries:
        return _result("rvc-models", "OK", "無已登記模型（跳過抽驗）")
    checked = 0
    for name in list(entries)[:3]:
        try:
            model_safety.verify_rvc_file(config.assets_root(), name, manifest)
            checked += 1
        except Exception as exc:
            return _result("rvc-models", "FAIL", "%s: %s" % (name, str(exc)[:150]))
    return _result("rvc-models", "OK", "抽驗 %d 個模型 SHA-256" % checked)


def check_workspace_writable(workspace=None, probe_name=None):
    workspace = workspace or config.workspace_root()
    out_dir = os.path.join(workspace, "output")
    probe = probe_name or ".doctor_probe"
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe_path = os.path.join(out_dir, probe)
        with open(probe_path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe_path)
        return _result("workspace", "OK", "output/ 可寫")
    except Exception as exc:
        return _result("workspace", "FAIL", str(exc)[:200])


CHECKS = [
    check_python,
    check_package,
    check_torch,
    check_fairseq,
    check_ffmpeg,
    check_api_key,
    check_characters,
    check_manifest,
    check_rvc_models,
    check_workspace_writable,
]


def main(argv=None):
    results = [fn() for fn in CHECKS]
    for item in results:
        line = "[%s] %s" % (item["status"], item["name"])
        if item["detail"]:
            line += " — %s" % item["detail"]
        print(line)
    counts = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print("summary: %d OK / %d WARN / %d FAIL"
          % (counts.get("OK", 0), counts.get("WARN", 0), counts.get("FAIL", 0)))
    return 1 if counts.get("FAIL", 0) else 0


if __name__ == "__main__":
    sys.exit(main())