#!/usr/bin/env python3
"""rag_build.py - build/update the LearnTok AI RAG knowledge base (ChromaDB).

Reads source materials (markdown/txt, script JSON, SRT), chunks them
(Chinese-aware, 100-200 chars with overlap), embeds, and upserts into
ChromaDB with metadata {topic, source, doc_id, chunk_index}.

Usage:
  python -m learntok.tools.rag_build --source materials/ --topic finance
  python -m learntok.tools.rag_build --source notes.md --source script_x.json --topic science
  python -m learntok.tools.rag_build --source materials/genai-beginners/zh-HK/04-prompt-engineering-fundamentals --topic genai-04-prompt-engineering-fundamentals --series genai-beginners
  python -m learntok.tools.rag_build --list-topics
  python -m learntok.tools.rag_build --source materials/ --dry-run
  python -m learntok.tools.rag_build --source . --embedder st   # semantic (needs sentence-transformers)
"""
import argparse
import hashlib
import os
import sys

from learntok import config
from learntok.tools import rag_common as rag

BATCH = 64


def collect_sources(paths):
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for n in sorted(names):
                    if n.lower().endswith((".md", ".txt", ".markdown", ".json", ".srt")):
                        files.append(os.path.join(root, n))
        else:
            print("  warning: not found: %s" % p)
    return sorted(set(os.path.abspath(f) for f in files))


def main():
    ap = argparse.ArgumentParser(description="Build/update the RAG knowledge base (ChromaDB).")
    ap.add_argument("--source", action="append",
                    help="source file or directory (repeatable); --list-topics 時可省略")
    ap.add_argument("--topic", default="general",
                    help="topic tag（子課程粒度，如 genai-04-prompt-engineering-fundamentals）")
    ap.add_argument("--series", default=None,
                    help="series tag（教育系列，如 genai-beginners；預設同 --topic）")
    ap.add_argument("--list-topics", action="store_true",
                    help="列出知識庫現有 series/topic 與 chunk 數後結束（不需 --source）")
    ap.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    ap.add_argument("--embedder", default="auto", choices=["auto", "st", "openai", "hash"],
                    help="embedding backend (auto: st -> openai -> hash)")
    ap.add_argument("--chunk-size", type=int, default=200)
    ap.add_argument("--overlap", type=int, default=40)
    ap.add_argument("--db", default=rag.DEFAULT_DB_PATH, help="ChromaDB path")
    ap.add_argument("--dry-run", action="store_true", help="print plan only")
    args = ap.parse_args()

    if not args.source and not args.list_topics:
        ap.error("--source 或 --list-topics 至少要給一個")

    if args.list_topics:
        client = rag.get_client(args.db)
        col = rag.get_collection(client, args.collection)
        data = col.get(include=["metadatas"])
        from collections import Counter
        by_series, by_topic = Counter(), Counter()
        for m in data["metadatas"]:
            m = m or {}
            by_series[m.get("series") or "(無系列)"] += 1
            by_topic[m.get("topic") or "(無主題)"] += 1
        print("collection: %s    total chunks: %d" % (args.collection, len(data["ids"])))
        print("--- series ---")
        for k, v in by_series.most_common():
            print("%6d  %s" % (v, k))
        print("--- topic ---")
        for k, v in by_topic.most_common():
            print("%6d  %s" % (v, k))
        return

    embed_fn, dims = rag.make_embedder(args.embedder)
    files = collect_sources(args.source)
    if not files:
        sys.exit("error: no supported source files found")

    plan = []
    total_chunks = 0
    for f in files:
        text = rag.extract_text(f)
        if not text or not text.strip():
            print("  skip (no extractable text): %s" % f)
            continue
        chunks = rag.chunk_text(text, args.chunk_size, args.overlap)
        if not chunks:
            continue
        rel = os.path.relpath(f, config.workspace_root()).replace(os.sep, "/")
        plan.append((f, rel, chunks))
        total_chunks += len(chunks)
        print("  %-58s %3d chunks / %5d chars" % (rel, len(chunks), len(text)))

    if not plan:
        sys.exit("error: nothing to index")
    if args.dry_run:
        print("dry-run: %d file(s), %d chunk(s) -> %s (collection=%s, embedder=%s, dims=%d)"
              % (len(plan), total_chunks, args.db, args.collection, args.embedder, dims))
        return

    client = rag.get_client(args.db)
    col = rag.get_collection(client, args.collection, dims, create=True)

    ids, docs, metas = [], [], []
    for _f, rel, chunks in plan:
        prefix = hashlib.md5(rel.encode("utf-8")).hexdigest()[:12]
        for i, c in enumerate(chunks):
            ids.append("%s:%d" % (prefix, i))
            docs.append(c)
            metas.append({"topic": args.topic, "series": args.series or args.topic,
                          "source": rel, "doc_id": os.path.basename(rel), "chunk_index": i})

    for b in range(0, len(docs), BATCH):
        sub = docs[b:b + BATCH]
        vecs = embed_fn(sub)
        col.upsert(ids=ids[b:b + BATCH], documents=sub,
                   embeddings=vecs, metadatas=metas[b:b + BATCH])

    print("done: %d chunks upserted into '%s' (series=%s, topic=%s, db=%s, embedder=%s)"
          % (len(docs), args.collection, args.series or args.topic, args.topic,
             args.db, args.embedder))


if __name__ == "__main__":
    main()