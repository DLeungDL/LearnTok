#!/usr/bin/env python3
"""rag_backfill_series.py — 為舊 chunks 補上 series 標記（metadata-only，不重新 embed）。

系列式 RAG 上線前，舊 chunks 只有 topic（子課程）沒有 series（系列）。
本工具直接對 ChromaDB 做 metadata-only update，把 series 補上；
之後新素材一律用 rag_build.py --series <名> 建庫，不需要再跑本工具。

用法：
  .venv\Scripts\python.exe -m learntok.tools.rag_backfill_series --series genai-beginners
  .venv\Scripts\python.exe -m learntok.tools.rag_backfill_series --series genai-beginners --topic-filter genai-00-course-setup
  .venv\Scripts\python.exe -m learntok.tools.rag_backfill_series --series genai-beginners --dry-run
"""
import argparse
import os
import sys

from learntok.tools import rag_common as rag


def main():
    ap = argparse.ArgumentParser(description="Backfill series metadata onto existing chunks.")
    ap.add_argument("--series", required=True, help="系列名稱（如 genai-beginners）")
    ap.add_argument("--topic-filter", default=None,
                    help="只處理指定 topic 的 chunks（預設全部）")
    ap.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    ap.add_argument("--db", default=rag.DEFAULT_DB_PATH, help="ChromaDB path")
    ap.add_argument("--dry-run", action="store_true", help="只預覽不寫入")
    args = ap.parse_args()

    client = rag.get_client(args.db)
    col = rag.get_collection(client, args.collection)
    data = col.get(include=["metadatas"])

    ids, metas = [], []
    for i, m in enumerate(data["metadatas"]):
        m = m or {}
        if m.get("series") == args.series:
            continue
        if args.topic_filter and m.get("topic") != args.topic_filter:
            continue
        new_m = dict(m)
        new_m["series"] = args.series
        ids.append(data["ids"][i])
        metas.append(new_m)

    if not ids:
        print("nothing to update")
        return
    note = "（topic=%s）" % args.topic_filter if args.topic_filter else ""
    print("將更新 %d chunks → series=%s%s" % (len(ids), args.series, note))
    if args.dry_run:
        print("dry-run：未寫入")
        return

    for b in range(0, len(ids), 512):
        col.update(ids=ids[b:b + 512], metadatas=metas[b:b + 512])
    print("done：%d chunks series=%s%s" % (len(ids), args.series, note))


if __name__ == "__main__":
    main()