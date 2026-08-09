#!/usr/bin/env python3
"""rag_retrieve.py - retrieve top-k chunks from the RAG knowledge base.

Usage:
  python -m learntok.tools.rag_retrieve --query "流動性溢價是什麼"
  python -m learntok.tools.rag_retrieve --script pipeline/examples/script_xxx.json --k 5
  python -m learntok.tools.rag_retrieve --query "代理問題" --topic finance
  python -m learntok.tools.rag_retrieve --query "..." --embedder st
"""
import argparse
import json
import os
import sys

from learntok.tools import rag_common as rag


def derive_query(script_path):
    with open(script_path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    parts = [data.get("title", ""), data.get("id", "")]
    return " ".join(p for p in parts if p)


def main():
    ap = argparse.ArgumentParser(description="Retrieve top-k chunks from the RAG knowledge base.")
    ap.add_argument("--query", help="free-text query")
    ap.add_argument("--script", help="script JSON; query is derived from its title/id")
    ap.add_argument("--topic", default=None, help="filter by topic tag (optional)")
    ap.add_argument("--collection", default=rag.DEFAULT_COLLECTION)
    ap.add_argument("--embedder", default="auto", choices=["auto", "st", "openai", "hash"])
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--db", default=rag.DEFAULT_DB_PATH, help="ChromaDB path")
    args = ap.parse_args()

    if args.query and args.script:
        sys.exit("error: use --query OR --script, not both")
    if not args.query and not args.script:
        sys.exit("error: need --query or --script")

    query = args.query if args.query else derive_query(args.script)
    embed_fn, _dims = rag.make_embedder(args.embedder)

    client = rag.get_client(args.db)
    col = rag.get_collection(client, args.collection)
    vec = embed_fn([query])[0]
    where = {"topic": args.topic} if args.topic else None
    res = col.query(query_embeddings=[vec], n_results=args.k, where=where,
                    include=["documents", "metadatas", "distances"])

    hits = res["ids"][0]
    if not hits:
        print("no results (query=%s, topic=%s)" % (query, args.topic or "any"))
        return

    print("query: %s" % query)
    print("hits: %d (collection=%s, topic=%s)\n" % (len(hits), args.collection, args.topic or "any"))
    for i, hit_id in enumerate(hits):
        dist = res["distances"][0][i]
        meta = res["metadatas"][0][i]
        doc = res["documents"][0][i]
        score = 1.0 - dist
        print("[%2d] score=%.3f  %s (topic=%s, chunk=%s)"
              % (i + 1, score, meta.get("source"), meta.get("topic"), meta.get("chunk_index")))
        print("     %s\n" % doc[:140])


if __name__ == "__main__":
    main()