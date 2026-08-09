"""Quick single-line TTS test — verifies edge-tts connectivity + proxy.

Usage:
  python -m learntok.tools.tts_test
  python -m learntok.tools.tts_test --proxy http://127.0.0.1:7890
  python -m learntok.tools.tts_test --proxy socks5://127.0.0.1:1080
"""
import argparse, asyncio, os, time

async def test(proxy):
    import edge_tts
    out = os.path.join(os.getcwd(), "tts_test_output.mp3")
    t0 = time.time()
    c = edge_tts.Communicate("你好，這是語音測試。", "zh-CN-YunxiNeural", rate="+12%", proxy=proxy)
    await c.save(out)
    sz = os.path.getsize(out)
    print("SUCCESS: %s (%d bytes, %.1fs)" % (out, sz, time.time() - t0))

def main():
    ap = argparse.ArgumentParser(description="Quick edge-tts connectivity test")
    ap.add_argument("--proxy", default=None, help="proxy URL")
    args = ap.parse_args()
    print("Proxy:", args.proxy or "(none — direct)")
    try:
        asyncio.run(test(args.proxy))
    except Exception as e:
        print("FAILED:", type(e).__name__, str(e)[:300])

if __name__ == "__main__":
    main()
