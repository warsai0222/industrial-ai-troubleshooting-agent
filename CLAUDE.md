# Plant Troubleshooting Assistant — System Reference

This project connects Claude Code to four data sources for a coal-fired CFB boiler
(Hu et al. 2025, *Scientific Data*, DOI 10.1038/s41597-025-05096-4) via a local MCP
server (`mcp_server/`, registered in `.mcp.json`): a **historian** (5 days of
sensor data), a **DCS alarm log** (6,005 real alarm events), a **process knowledge
graph**, and a **plant document library** (62 markdown docs, semantic search). Together
they support natural-language root-cause investigations that explicitly distinguish
cause from symptom and log every step for SME review.

This file documents what was actually built and verified in this repository. (An
earlier version of this file described a more built-out system that didn't exist on
disk — disregard anything you may recall from that; this is the real one.)

---

## Critical Rules — Read First

1. **Never invent a tag name, doc_id, or equipment id.** Confirm with
   `historian_list_tags`, `docs_list`, or `kg_list_equipment`/`kg_query_equipment`
   first if uncertain.
2. **Always resolve the time window via `historian_get_window` before any historian or
   alarm query.** The dataset is a **static 5-day snapshot**
   (2022-03-27 14:28:54 → 2022-04-01 14:28:49), not a live feed — there is no "now".
   Anchor relative phrases ("the last 3 hours") to the window's end.
3. **Always cite docs with title + revision** when quoting a procedure or limit:
   *"Per [Document Title], Rev [X.X]: ..."*
4. **A `null` `normal_min`/`normal_max` bound means "no known DCS threshold on that
   side," not "any value is fine."** About a third of the 30 tags only have a setpoint
   on one side. Always check `range_source_min`/`range_source_max`
   (`dcs_setpoint` = real, verified threshold; `observed_bound_no_alarms` = this tag
   never alarmed in the 5-day window, so its full observed range is used as a
   placeholder — a much weaker signal). See `historian_list_tags`.
5. **`doc_type` values are singular, not the plural folder names:** `controls`,
   `datasheet` (not `datasheets`), `maintenance`, `safety`, `sop` (not `sops`),
   `troubleshooting`.
6. **Equipment ids come in two vocabularies** — the knowledge graph's own node ids and
   the RAG docs' `equipment:` frontmatter vocabulary (16 ids). Every tool that takes
   an `equipment_id`/`equipment` param resolves either one automatically via
   `mcp_server/equipment_crosswalk.py`; you don't need to know which one a given id
   belongs to. A few docs ids (`economizer`, `secondary_air_preheater`,
   `furnace_chamber`, `furnace_lower_hearth`) resolve to more than one KG node —
   check `resolved_ids` in the response.
7. **Investigation logging is real-time, not a post-hoc summary.** Call
   `audit_start_investigation` first, pass the returned `investigation_id` into every
   subsequent historian/alarm/kg/docs tool call (it's an optional param — when given,
   the call is auto-logged as a numbered step) and into every `audit_log_reasoning`
   call. Never reconstruct the log from memory afterward.
8. **Distinguish root cause from symptom explicitly.** Before any `conclusion`, log a
   `cause_vs_symptom_check` reasoning step. The `investigation-report` skill flags
   conclusions that skip this.

---

## The Four Data Sources

### 1. Historian — `boiler_historian.duckdb`
- `historian_data`: `timestamp` + 30 tag columns (DOUBLE), 86,400 rows at 5-second
  intervals.
- `tags`: one row per tag — `tag_name, description, source_description, units,
  normal_min, normal_max, range_source_min, range_source_max`. Ranges are derived from
  real DCS alarm setpoints in `boiler_alarms.duckdb` wherever available (see Critical
  Rule #4).
- `TE_8332A` (boiler outlet steam temperature) is the primary KPI — real DCS band
  528–548°C (`Lo`=528, `Hi`=548).

### 2. Alarm log — `boiler_alarms.duckdb`
- Single table `alarm_events`, 6,005 rows:
  `alarm_id, tag_name, alarm_level, priority, message, setpoint, activated_at,
  activated_value, acknowledged_at, cleared_at, duration_sec, max_deviation`.
- `alarm_level`: low direction `Lo`/`LoLo`; high direction `Hi`/`HiHi`/`HiHiHi`
  (unused in this window) and the vibration-style escalation `Alert`/`Alarm`/`Trip`.
- `priority`: `Critical`={Trip,HiHiHi,LoLo}, `High`={HiHi,Alarm}, `Medium`={Hi,Lo},
  `Low`={Alert} (exactly, verified against real counts).
- "Active" = `cleared_at IS NULL` (only 21 of 6,005 rows — most alarms clear).
- Only 22 of the 30 historian tags ever alarm in this window; the other 8
  (`PT_8313A`, `PT_8313B`, `PTCA_8322A`, `PTCA_8324`, `TE_8303`, `TV_8329ZC`,
  `YFJ3_AI`, `YJJWSLL`) having zero alarms is expected, not an error.
- **Known data characteristic:** 10 tags (`FT_8301`, `FT_8302`, `FT_8306A`,
  `FT_8306B`, `SXLTCYZ`, `SXLTCYY`, `YCLCCY`, `ZCLCCY`, `TE_8319A`, `TE_8319B`) spend
  almost the entire 5-day window on the alarm side of their configured setpoint — some
  via one continuous never-cleared alarm starting at the very first timestamp. Treat
  `pct_time_out_of_normal_range` near 100% for these as "chronically outside its
  configured DCS setpoint for this whole dataset," not as a discrete event with a
  meaningful start time within the window.

### 3. Knowledge graph — `boiler_kg.json` (docs: `boiler_kg.md`)
- 64 nodes / 98 edges, networkx-compatible node-link JSON. Equipment, valves, all 30
  sensors, 4 control-loop/process-relationship nodes, 2 boundary nodes.
- `CONNECTS_TO` edges carry `stream` + `hop` for 9 modeled process streams (`Coal`,
  `FlueGas`, `Solids`, `PrimaryAir`, `SecondaryAir`, `FeedWater`, `Steam`,
  `DesuperheatingWater`, `ContinuousBlowdown`).
- Regenerate with `.venv/bin/python scripts/build_kg.py` after editing
  `scripts/build_kg.py` (it reads sensor ranges live from `boiler_historian.duckdb`,
  so the two never drift apart).

### 4. Plant document library — `RAG docs/` + `rag_vector_db/`
- 62 markdown docs across `controls/`, `datasheets/`, `maintenance/`, `safety/`,
  `sops/`, `troubleshooting/`; 434 chunks (one per `##` section) in a ChromaDB
  collection `plant_docs`, embedded with a local `BAAI/bge-small-en-v1.5` model.
- Rebuild after editing docs: `.venv/bin/python build_docs_db.py` (incremental) or
  `--rebuild` (wipe and start over).

---

## MCP Tools (`mcp_server/`, 26 tools)

**Historian** (`historian_tools.py`): `historian_list_tags`, `historian_get_window`,
`historian_get_series` (auto-downsamples above ~2000 pts/tag), `historian_get_stats`,
`historian_find_excursions` (contiguous out-of-range runs — the core "when did this
actually start" tool).

**Alarms** (`alarm_tools.py`): `alarm_query`, `alarm_get_stats`, `alarm_get_active_at`,
`alarm_get_context_window` (±N min around a timestamp — primary "what else was
happening" tool), `alarm_detect_floods` (ISA-18.2-style; the earliest activation in a
flood is the lead, not the highest-priority one).

**Knowledge graph** (`kg_tools.py`): `kg_query_equipment`, `kg_list_equipment`,
`kg_get_sensors_for_equipment`, `kg_get_upstream_equipment`,
`kg_get_downstream_equipment`, `kg_trace_stream`, `kg_find_path`,
`kg_get_related_sensors`, `kg_get_control_loop`, `kg_get_upstream_sensors` (**the**
root-cause-support tool — walks upstream from a sensor's equipment collecting every
sensor found along the way).

**Docs/RAG** (`docs_tools.py`): `docs_search` (semantic, filterable by `doc_type`,
`equipment`, `sensor_tag`), `docs_get_document`, `docs_list`.

**Audit** (`audit_tools.py`): `audit_start_investigation`, `audit_log_reasoning`
(`reasoning_type` ∈ hypothesis/observation/conclusion/rejection/corrective_action/
cause_vs_symptom_check; `confidence` required on `conclusion`), `audit_end_investigation`.

Run the server directly for debugging: `.venv/bin/python -m mcp_server.server`.

---

## Investigation Workflow

1. `audit_start_investigation(title, initial_symptom)` → `investigation_id`.
2. `historian_get_window()` to anchor any relative time.
3. Use historian/alarm/kg/docs tools with `investigation_id` set on each call — this
   logs every call as a numbered step automatically.
4. Log `hypothesis`/`observation` steps via `audit_log_reasoning` as you go, citing
   prior step numbers in `evidence_steps`.
5. When a variable looks implicated: log a `cause_vs_symptom_check` — is it driven by
   something already observed upstream (`kg_get_upstream_sensors` +
   `historian_find_excursions`/`alarm_get_context_window` on those tags), or is it the
   earliest abnormality found so far? Keep walking upstream until the trail goes cold.
6. Log the `conclusion` with `confidence` (≥0.8 HIGH / 0.5–0.79 MEDIUM / <0.5 LOW —
   never present a root cause as definitive while a credible alternative is still
   open; lower confidence instead) and `evidence_steps`.
7. Log at least one `corrective_action`, grounded in a `docs_search` result (cite
   title + revision).
8. `audit_end_investigation(investigation_id, final_status)`.
9. Invoke the **`investigation-report`** skill — it runs
   `scripts/render_investigation_report.py` to deterministically render
   `investigations/<id>/report.md` (full SME audit trail) and `summary.md` (root
   cause, confidence, causal chain, corrective actions). Always run this for a
   completed investigation — don't hand-write a summary instead.

For simple lookups that aren't a root-cause investigation, call tools directly without
an `investigation_id` — logging is opt-in per call, not global.

---

## Query Workflows

**Time-series question** ("what has X been doing") → `historian_get_window` →
`historian_get_series` (or `historian_get_stats` for a single-number summary).

**Statistical question** ("average/min/max of X") → `historian_get_stats`, compare
`avg`/`min`/`max` against `normal_min`/`normal_max` and check `range_source_min/max`.

**Anomaly timing question** ("when did X go abnormal") →
`historian_find_excursions(direction=...)`.

**Alarm question** ("what alarms fired around time T") → `alarm_get_context_window`;
for a full list of everything active at one instant, `alarm_get_active_at`.

**Topology question** ("what's upstream of X", "trace the steam path") →
`kg_get_upstream_equipment` / `kg_trace_stream` / `kg_find_path`.

**Documentation question** ("what's the procedure for...", "what's the limit for...")
→ `docs_search`, narrowed by `doc_type`/`equipment`/`sensor_tag` if known; full text
via `docs_get_document` after the right `doc_id` is identified.

**Root-cause investigation** ("why is X happening", "what could be causing this") →
the full Investigation Workflow above.

---

## Environment

Project-local venv at `.venv/` (Python 3.13 — not the system 3.15, which lacks
`onnxruntime` wheels needed by `chromadb`), dependencies pinned in `requirements.txt`.
`mcp` is pinned to `1.30.0` (the `mcp>=2.0` line renamed `FastMCP` to `MCPServer` with
breaking changes — this codebase uses the `FastMCP` decorator API throughout).

```
mcp_server/            MCP server package (server.py is the entrypoint, -m mcp_server.server)
scripts/
  build_kg.py            regenerate boiler_kg.json
  render_investigation_report.py   renders report.md/summary.md for the investigation-report skill
build_docs_db.py       rebuild rag_vector_db/ from RAG docs/
investigations/        one subdirectory per investigation (audit.db, report.md, summary.md)
.claude/skills/investigation-report/   the report-rendering skill
```
