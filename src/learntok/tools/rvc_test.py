"""Quick single-line RVC test — converts one TTS line to verify pipeline.

Usage:
  python -m learntok.tools.rvc_test
"""
import os, sys, shutil, subprocess, tempfile

from learntok import config
from learntok.tools import model_safety

def find_tool(name):
    return config.find_tool(name, exit_on_missing=False)

def main():
    import torch
    from rvc_python.infer import RVCInference

    assets_root = config.assets_root()
    os.environ["LEARNTOK_ASSETS_ROOT"] = assets_root
    lib_dir = os.path.dirname(__import__("rvc_python").__file__)
    ffmpeg = find_tool("ffmpeg")

    manifest = model_safety.load_manifest(assets_root)

    # Download base models if needed
    from rvc_python.download_model import download_rvc_models
    download_rvc_models(lib_dir)
    print("base models ready")

    # Verify base models after download (download_rvc_models has no hash check)
    try:
        model_safety.verify_base_model(os.path.join(lib_dir, "base_model"),
                                       "hubert_base.pt", manifest)
        print("hubert_base.pt integrity OK")
    except model_safety.ModelSafetyError as e:
        print("error: %s" % e)
        sys.exit(1)

    # Load model (verified against manifest before it reaches torch.load)
    model_path = model_safety.verify_rvc_file(assets_root, "rvc_deng_v1.pth", manifest)
    index_path = model_safety.verify_rvc_file(assets_root, "rvc_deng_v1.index", manifest)

    print("loading RVC model...")
    rvc = RVCInference(
        model_path=model_path,
        index_path=index_path,
        device="cuda:0",
        version="v2",
    )
    rvc.set_params(f0up_key=0, f0method="rmvpe", index_ratio=0.5)
    print("model loaded")

    # Pick first speaker-A line
    import json
    script = json.load(open("pipeline/examples/script_public_vs_private.json", encoding="utf-8"))
    test_line = None
    for ln in script["lines"]:
        if ln.get("speaker") == "A" and ln.get("audio_file"):
            test_line = ln
            break

    if not test_line:
        print("no speaker A line found")
        return

    mp3_path = os.path.join(assets_root, test_line["audio_file"])
    print("input:", mp3_path)

    # Convert: MP3 -> WAV -> RVC -> WAV -> MP3
    tmp = tempfile.mkdtemp(prefix="rvc_test_")
    wav_in = os.path.join(tmp, "in.wav")
    wav_out = os.path.join(tmp, "out.wav")
    mp3_out = os.path.join(os.getcwd(), "rvc_test_output.mp3")

    subprocess.run([ffmpeg, "-y", "-i", mp3_path, "-ar", "44100", "-ac", "1", wav_in],
                   capture_output=True, check=True)
    print("converted to wav, running RVC...")

    rvc.infer_file(wav_in, wav_out)
    print("RVC done, converting back to mp3...")

    subprocess.run([ffmpeg, "-y", "-i", wav_out, "-b:a", "192k", mp3_out],
                   capture_output=True, check=True)
    shutil.rmtree(tmp, ignore_errors=True)

    sz_in = os.path.getsize(mp3_path)
    sz_out = os.path.getsize(mp3_out)
    print("SUCCESS: %s (%d -> %d bytes)" % (mp3_out, sz_in, sz_out))

if __name__ == "__main__":
    main()
