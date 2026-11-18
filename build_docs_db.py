"""Build/refresh the plant-document vector database (rag_vector_db/) from RAG docs/.

Each markdown file has YAML frontmatter (doc_id, doc_type, equipment, tags, title,
revision, date) followed by a body split into H2 (`## `) sections. Each section becomes
one chunk, embedded with a local BGE-small sentence-transformers model and stored in a
persistent ChromaDB collection for semantic search.

Usage:
    .venv/bin/python build_docs_db.py            # incremental: only changed files re-embedded
    .venv/bin/python build_docs_db.py --rebuild   # wipe and rebuild from scratch
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import chromadb
import yaml
from chromadb.utils import embedding_functions

DOCS_DIR = Path(__file__).parent / "RAG docs"
DB_DIR = Path(__file__).parent / "rag_vector_db"
COLLECTION_NAME = "plant_docs"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n(.*)", re.DOTALL)
H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def parse_doc(path: Path):
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: no YAML frontmatter found")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()
    return meta, body


def split_sections(body: str):
    """Split a doc body into (heading, section_text) chunks on H2 boundaries.
    Content before the first H2 (if any -- usually the H1 title + doc-number line)
    is kept and prepended to the first section so it isn't lost.
    """
    matches = list(H2_RE.finditer(body))
    if not matches:
        return [("(full document)", body)]
    sections = []
    preamble = body[: matches[0].start()].strip()
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        heading = m.group(1).strip()
        section_text = body[start:end].strip()
        if i == 0 and preamble:
            section_text = preamble + "\n\n" + section_text
        sections.append((heading, section_text))
    return sections


def join_list(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v) if v is not None else ""


def build(rebuild: bool):
    files = sorted(DOCS_DIR.rglob("*.md"))
    if not files:
        print(f"No .md files found under {DOCS_DIR}", file=sys.stderr)
        sys.exit(1)

    client = chromadb.PersistentClient(path=str(DB_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)

    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    collection = client.get_or_create_collection(COLLECTION_NAME, embedding_function=ef)

    existing = collection.get(include=["metadatas"])
    existing_hash_by_doc = {}
    for cid, md in zip(existing["ids"], existing["metadatas"]):
        existing_hash_by_doc.setdefault(md["doc_id"], md.get("source_hash"))

    ids, documents, metadatas = [], [], []
    seen_doc_ids = set()
    skipped, changed = 0, 0

    for path in files:
        meta, body = parse_doc(path)
        doc_id = meta["doc_id"]
        seen_doc_ids.add(doc_id)
        source_hash = hashlib.sha256((meta.get("title", "") + body).encode()).hexdigest()[:16]

        if not rebuild and existing_hash_by_doc.get(doc_id) == source_hash:
            skipped += 1
            continue
        changed += 1

        if doc_id in existing_hash_by_doc:
            old_ids = collection.get(where={"doc_id": doc_id})["ids"]
            if old_ids:
                collection.delete(ids=old_ids)

        title = meta.get("title", doc_id)
        doc_type = meta.get("doc_type", "")
        revision = meta.get("revision", "")
        equipment = join_list(meta.get("equipment"))
        tags = join_list(meta.get("tags"))

        for i, (heading, section_text) in enumerate(split_sections(body)):
            embed_text = f"{title} ({doc_type}, Rev {revision})\n{heading}\n\n{section_text}"
            ids.append(f"{doc_id}__chunk{i}")
            documents.append(embed_text)
            metadatas.append({
                "doc_id": doc_id,
                "doc_type": doc_type,
                "title": title,
                "revision": str(revision),
                "date": str(meta.get("date", "")),
                "equipment": equipment,
                "tags": tags,
                "section": heading,
                "chunk_index": i,
                "source_path": str(path.relative_to(DOCS_DIR.parent)),
                "source_hash": source_hash,
            })

    # Remove chunks for any doc files that were deleted since the last build
    stale_doc_ids = set(existing_hash_by_doc) - seen_doc_ids
    for doc_id in stale_doc_ids:
        stale_ids = collection.get(where={"doc_id": doc_id})["ids"]
        if stale_ids:
            collection.delete(ids=stale_ids)

    if documents:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    total = collection.count()
    print(f"docs scanned: {len(files)} | changed: {changed} | unchanged (skipped): {skipped} "
          f"| removed (stale): {len(stale_doc_ids)}")
    print(f"chunks added/updated: {len(documents)} | total chunks in collection: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="wipe and rebuild from scratch")
    args = parser.parse_args()
    build(rebuild=args.rebuild)
