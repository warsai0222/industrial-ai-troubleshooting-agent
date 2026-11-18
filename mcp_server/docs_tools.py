"""Docs/RAG tools -- rag_vector_db/ (ChromaDB PersistentClient, collection "plant_docs").

434 chunks from 62 markdown docs under RAG docs/{controls,datasheets,maintenance,
safety,sops,troubleshooting}/, one chunk per `##` section, embedded with a local
BAAI/bge-small-en-v1.5 model. Chunk metadata: doc_id, doc_type, title, revision, date,
equipment (comma-joined string), tags (comma-joined string, sensor tag names), section,
chunk_index, source_path, source_hash.

IMPORTANT: `doc_type` values are singular (controls, datasheet, maintenance, safety,
sop, troubleshooting) even though their source folders are plural (datasheets/, sops/)
-- don't filter on the plural folder name.

This chromadb version's `where` filter supports exact/$in/$eq-style matching on
metadata but NOT substring/$contains matching on metadata (confirmed empirically --
$contains only works via `where_document`, i.e. full-text search on chunk content, not
on a metadata field). Since `equipment`/`tags` are comma-joined strings, membership
filtering is done in Python against the (small, ~434-row) metadata set, then the
matching doc_ids are passed to the actual semantic query via a `where={"doc_id":
{"$in": [...]}}` filter.
"""
import chromadb
from chromadb.utils import embedding_functions

from mcp_server import config, session_store
from mcp_server.equipment_crosswalk import docs_equipment_terms

_collection = None
_all_metadatas_cache: list[dict] | None = None


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(config.VECTOR_DB_PATH))
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=config.EMBEDDING_MODEL)
        _collection = client.get_collection(config.VECTOR_DB_COLLECTION, embedding_function=ef)
    return _collection


def _all_metadatas() -> list[dict]:
    global _all_metadatas_cache
    if _all_metadatas_cache is None:
        got = _get_collection().get(include=["metadatas"])
        _all_metadatas_cache = got["metadatas"]
    return _all_metadatas_cache


def _matching_doc_ids(doc_type: str | None, equipment: str | None, sensor_tag: str | None) -> set[str] | None:
    """Returns the set of doc_ids satisfying all given filters, or None if no filters
    were given at all (meaning: don't restrict)."""
    if doc_type is None and equipment is None and sensor_tag is None:
        return None
    if doc_type is not None and doc_type not in config.DOC_TYPES:
        raise ValueError(f"doc_type must be one of {config.DOC_TYPES} (these are the actual "
                          f"metadata values, which are singular even though the source folders "
                          f"are plural, e.g. 'datasheet' not 'datasheets').")
    equipment_terms = set(docs_equipment_terms(equipment)) if equipment else None

    matched = set()
    for md in _all_metadatas():
        if doc_type is not None and md.get("doc_type") != doc_type:
            continue
        if equipment_terms is not None:
            doc_equip = {e.strip() for e in (md.get("equipment") or "").split(",") if e.strip()}
            if not (doc_equip & equipment_terms):
                continue
        if sensor_tag is not None:
            doc_tags = {t.strip() for t in (md.get("tags") or "").split(",") if t.strip()}
            if sensor_tag not in doc_tags:
                continue
        matched.add(md["doc_id"])
    return matched


def register(mcp):
    @mcp.tool()
    def docs_search(
        query: str,
        doc_type: str | None = None,
        equipment: str | None = None,
        sensor_tag: str | None = None,
        n_results: int = 5,
        investigation_id: str | None = None,
    ) -> list[dict]:
        """Semantic search over the 62 plant documents (434 chunks), returning ranked
        sections with full citation metadata. doc_type must be one of: controls,
        datasheet, maintenance, safety, sop, troubleshooting (singular values, not the
        plural folder names). equipment accepts either the RAG-docs equipment vocabulary
        or a raw knowledge-graph node id (translated automatically). sensor_tag matches
        an exact historian tag name. Always cite results as "Per <title>, Rev <revision>:
        ..." when quoting a procedure or limit."""
        matched = _matching_doc_ids(doc_type, equipment, sensor_tag)
        if matched is not None and not matched:
            if investigation_id:
                session_store.log_tool_step(investigation_id, "docs_search",
                                             {"query": query, "doc_type": doc_type, "equipment": equipment,
                                              "sensor_tag": sensor_tag}, {"n_results": 0, "reason": "no docs matched filters"})
            return []

        where = {"doc_id": {"$in": sorted(matched)}} if matched is not None else None
        kwargs = {"query_texts": [query], "n_results": n_results}
        if where is not None:
            kwargs["where"] = where
        res = _get_collection().query(**kwargs)

        results = []
        for doc, md, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            results.append({
                "doc_id": md["doc_id"], "title": md["title"], "doc_type": md["doc_type"],
                "revision": md["revision"], "date": md["date"], "section": md["section"],
                "equipment": md["equipment"], "tags": md["tags"], "text": doc, "distance": dist,
            })
        if investigation_id:
            session_store.log_tool_step(
                investigation_id, "docs_search",
                {"query": query, "doc_type": doc_type, "equipment": equipment, "sensor_tag": sensor_tag},
                {"n_results": len(results), "doc_ids": [r["doc_id"] for r in results]},
            )
        return results

    @mcp.tool()
    def docs_get_document(doc_id: str, investigation_id: str | None = None) -> dict:
        """Full text of one document, reassembled from its chunks in order, with
        frontmatter metadata. Use after docs_search has already identified the right
        doc_id and you need the complete procedure/datasheet, not just the matched
        section."""
        got = _get_collection().get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
        if not got["ids"]:
            raise ValueError(f"No document with doc_id {doc_id!r}. Use docs_list to see valid doc_ids.")
        pairs = sorted(zip(got["metadatas"], got["documents"]), key=lambda p: p[0]["chunk_index"])
        first_md = pairs[0][0]
        full_text = "\n\n".join(doc for _, doc in pairs)
        result = {
            "doc_id": doc_id, "title": first_md["title"], "doc_type": first_md["doc_type"],
            "revision": first_md["revision"], "date": first_md["date"],
            "equipment": first_md["equipment"], "tags": first_md["tags"],
            "source_path": first_md["source_path"], "text": full_text, "n_sections": len(pairs),
        }
        if investigation_id:
            session_store.log_tool_step(investigation_id, "docs_get_document", {"doc_id": doc_id},
                                         {"title": first_md["title"], "n_sections": len(pairs)})
        return result

    @mcp.tool()
    def docs_list(doc_type: str | None = None, equipment: str | None = None, investigation_id: str | None = None) -> list[dict]:
        """Deduplicated index of documents (doc_id, title, doc_type, equipment, tags,
        revision, date), optionally filtered by doc_type and/or equipment. Use to browse
        what's available or confirm a doc_id before calling docs_get_document."""
        matched = _matching_doc_ids(doc_type, equipment, None)
        seen = {}
        for md in _all_metadatas():
            if matched is not None and md["doc_id"] not in matched:
                continue
            if md["doc_id"] not in seen:
                seen[md["doc_id"]] = {
                    "doc_id": md["doc_id"], "title": md["title"], "doc_type": md["doc_type"],
                    "equipment": md["equipment"], "tags": md["tags"], "revision": md["revision"],
                    "date": md["date"],
                }
        result = sorted(seen.values(), key=lambda d: d["doc_id"])
        if investigation_id:
            session_store.log_tool_step(investigation_id, "docs_list",
                                         {"doc_type": doc_type, "equipment": equipment}, {"n_docs": len(result)})
        return result
