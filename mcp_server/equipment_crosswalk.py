"""Maps the RAG docs' `equipment:` frontmatter vocabulary onto boiler_kg.json node ids.

These two vocabularies were built independently (the docs are fixed external content;
the KG was derived from the process flow diagram) and don't fully agree:

- Pure naming mismatches (same referent): already fixed by renaming the KG nodes
  directly in scripts/build_kg.py (cyclone_separator_left/right, steam_outlet).
- Genuine granularity mismatches: the KG models two physical components where the
  docs use one generic id. `economizer` covers both economiser stages;
  `secondary_air_preheater` covers both air-preheater stages; `furnace_chamber` and
  `furnace_lower_hearth` both refer to the single `furnace` KG node, since
  ds_cfb_furnace.md's sensor list has no distinct upper/lower zone assignment to
  split the KG node on.

Every KG tool and the docs `equipment` filter should resolve through
`resolve_equipment_id` before touching their underlying store, so a caller can pass
either vocabulary.
"""

DOCS_TO_KG = {
    "chimney": ["chimney"],
    "cyclone_separator_left": ["cyclone_separator_left"],
    "cyclone_separator_right": ["cyclone_separator_right"],
    "economizer": ["upper_stage_economiser", "lower_stage_economiser"],
    "furnace_chamber": ["furnace"],
    "furnace_lower_hearth": ["furnace"],
    "high_temp_superheater": ["high_temp_superheater"],
    "induced_draft_fan": ["induced_draft_fan"],
    "low_temp_superheater": ["low_temp_superheater"],
    "primary_air_preheater": ["primary_air_preheater"],
    "primary_desuperheater": ["primary_desuperheater"],
    "primary_fan": ["primary_fan"],
    "secondary_air_preheater": ["secondary_air_preheater_1", "secondary_air_preheater_2"],
    "secondary_fan": ["secondary_fan"],
    "steam_drum": ["steam_drum"],
    "steam_outlet": ["steam_outlet"],
}


# Reverse of DOCS_TO_KG: KG node id -> docs-vocabulary id(s) that refer to it. Used to
# go the other direction -- given a KG id (or a docs id, which simply isn't a key here
# and falls back to itself), find the RAG-docs `equipment:` value(s) to search for in
# doc metadata (see docs_equipment_terms / docs_tools.docs_search).
KG_TO_DOCS: dict[str, list[str]] = {}
for _docs_id, _kg_ids in DOCS_TO_KG.items():
    for _kg_id in _kg_ids:
        KG_TO_DOCS.setdefault(_kg_id, []).append(_docs_id)


def docs_equipment_terms(id_in: str) -> list[str]:
    """Resolve a caller-supplied equipment id (either vocabulary) to the RAG docs'
    `equipment:` value(s) to search for. A docs-vocabulary id passed directly (e.g.
    'economizer') isn't a key in KG_TO_DOCS, so it falls through to itself unchanged,
    which is already correct since it IS a valid docs value. A raw KG id (e.g.
    'upper_stage_economiser') translates to its docs alias(es). An id with neither a
    KG_TO_DOCS entry nor any docs usage (e.g. 'coal_bin') falls back to itself too --
    harmless, since it simply won't match any doc's equipment field.
    """
    return KG_TO_DOCS.get(id_in, [id_in])


def resolve_equipment_id(id_in: str, known_kg_ids: set[str] | None = None) -> list[str]:
    """Resolve a caller-supplied equipment id (docs vocabulary or raw KG node id) to
    the list of actual boiler_kg.json node ids it refers to.

    If `known_kg_ids` is given (callers that already have the graph loaded, i.e.
    kg_tools) and `id_in` is neither in the docs vocabulary nor a known KG id, raises
    ValueError. If `known_kg_ids` is None (callers without the graph loaded, i.e.
    docs_tools), unrecognized ids are passed through unchanged rather than raising --
    filtering on an id no doc happens to reference is a valid (empty) result, not an
    error, and docs_tools shouldn't need to load the KG just to validate.
    """
    if id_in in DOCS_TO_KG:
        return DOCS_TO_KG[id_in]
    if known_kg_ids is None or id_in in known_kg_ids:
        return [id_in]
    raise ValueError(
        f"Unknown equipment id {id_in!r}: not in the docs vocabulary "
        f"({sorted(DOCS_TO_KG)}) and not a node in boiler_kg.json."
    )
