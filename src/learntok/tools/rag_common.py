#!/usr/bin/env python3
"""rag_common.py - shared helpers for the LearnTok AI RAG knowledge base.

Storage: ChromaDB (local persistent, no server). The planned migration to
PostgreSQL + pgvector happens in Phase 2 (App/Web backend); the collection
schema (chunk text + embedding + metadata) carries over unchanged.

Data layout (per chunk):
  id        = md5(source_rel_path)[:12]:chunk_index
  document  = chunk text (100-200 chars, Chinese-aware)
  embedding = vector from the selected embedder
  metadata  = {topic, source, doc_id, chunk_index}
"""
import hashlib
import json
import math
import os
import re

from learntok import config

DEFAULT_DB_PATH = os.path.join(config.assets_root(), "rag", "chroma")
DEFAULT_COLLECTION = "leantok_kb"
EMBED_DIMS = {"hash": 384}

_SENT_BOUNDS = re.compile(r"[。！？!?\n；;]")
_CLAUSE_BOUNDS = re.compile(r"[，,、]")


def load_text_file(path):
    """Read a UTF-8(-BOM) text file; returns str or None."""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def text_from_script_json(path):
    """Extract title + line text from a LearnTok script JSON."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    parts = []
    if data.get("title"):
        parts.append(data["title"])
    for ln in data.get("lines", []):
        t = ln.get("text", "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts) if parts else None


def text_from_srt(path):
    """Extract cue text only (drop indices/timestamps) from an SRT file."""
    text = load_text_file(path)
    if not text:
        return None
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 2 and "-->" in lines[1]:
            out.extend(lines[2:])
        else:
            out.extend(lines)
    return "\n".join(out)


def extract_text(path):
    """Extract plain text from a supported source file (md/txt/json/srt)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt", ".markdown"):
        return load_text_file(path)
    if ext == ".json":
        return text_from_script_json(path)
    if ext == ".srt":
        return text_from_srt(path)
    return None


def chunk_text(text, max_chars=200, overlap=40):
    """Split text into Chinese-aware chunks (prefer sentence/clause boundaries)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end >= n:
            chunks.append(text[start:])
            break
        cut = None
        for m in _SENT_BOUNDS.finditer(text, start + max_chars // 2, end):
            cut = m.end()
        if cut is None:
            for m in _CLAUSE_BOUNDS.finditer(text, start + max_chars // 2, end):
                cut = m.end()
        if cut is None:
            cut = end
        chunks.append(text[start:cut])
        nxt = cut - overlap
        start = nxt if nxt > start else cut
    return chunks


def _embed_hash(texts):
    out = []
    for t in texts:
        vec = [0.0] * EMBED_DIMS["hash"]
        for i, ch in enumerate(t):
            h = hashlib.md5(("%d|%s" % (i, ch)).encode("utf-8")).digest()
            idx = int.from_bytes(h[:2], "big") % EMBED_DIMS["hash"]
            vec[idx] += 1.0 if h[2] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


def _embed_openai(api_key):
    import urllib.request
    def embed(texts):
        out = []
        for i in range(0, len(texts), 64):
            batch = texts[i:i + 64]
            body = json.dumps({"model": "text-embedding-3-small", "input": batch}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/embeddings", data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for item in sorted(data["data"], key=lambda x: x["index"]):
                out.append(item["embedding"])
        return out
    return embed


_ST_SINGLETON = {"model": None, "dims": None}


def _get_st_model():
    if _ST_SINGLETON["model"] is None:
        # project-local HF cache (assets/rag is gitignored); keeps the model portable
        os.environ.setdefault("HF_HOME", os.path.join(config.assets_root(), "rag", "hf_cache"))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("intfloat/multilingual-e5-small")
        _ST_SINGLETON["model"] = model
        _ST_SINGLETON["dims"] = int(
            getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)())
    return _ST_SINGLETON["model"], _ST_SINGLETON["dims"]


def _make_st():
    # 模型以 module 級 singleton 快取，避免每次 validate / retrieve 都重載 weights
    model, dims = _get_st_model()

    def embed(texts):
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vecs]
    return embed, dims


def make_embedder(name="auto"):
    """Return (embed_fn, dims). embed_fn(list[str]) -> list[list[float]].

    name: auto | st | openai | hash
      auto   -> sentence-transformers if available, else OpenAI (needs
                OPENAI_API_KEY), else hash (smoke-test only, not semantic).
    """
    if name in ("auto", "st"):
        try:
            return _make_st()
        except Exception as exc:
            if name == "st":
                raise SystemExit("error: sentence-transformers unavailable: %s" % exc)
    if name in ("auto", "openai"):
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return _embed_openai(key), 1536
    if name in ("auto", "hash"):
        print("warning: using hash embedder (smoke-test only, not semantic)")
        return _embed_hash, EMBED_DIMS["hash"]
    raise SystemExit("error: unknown embedder '%s' (auto|st|openai|hash)" % name)


def get_client(path=DEFAULT_DB_PATH):
    try:
        import chromadb
    except ImportError:
        raise SystemExit("error: chromadb not installed. Run: pip install chromadb")
    os.makedirs(path, exist_ok=True)
    return chromadb.PersistentClient(path=path)


def get_collection(client, name=DEFAULT_COLLECTION, dims=None, create=False):
    if create:
        return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
    return client.get_collection(name=name)