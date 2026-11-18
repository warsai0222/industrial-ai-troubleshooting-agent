# Plant Troubleshooting Assistant

Claude Code connected, via a local MCP server, to four data sources for a coal-fired
CFB boiler (Hu et al. 2025, *Scientific Data*, DOI 10.1038/s41597-025-05096-4): a
**historian** (5 days of sensor data), a **DCS alarm log** (6,005 real alarm events), a
**process knowledge graph**, and a **plant document library** (62 markdown docs,
semantic search). Together they support natural-language root-cause investigations
that distinguish cause from symptom and log every step for SME review.

See [CLAUDE.md](CLAUDE.md) for the full reference: critical usage rules, tool list,
data schemas, and the investigation workflow. This file covers setup and, below,
what it takes to point the same scaffold at a different plant.

## Project layout

```
mcp_server/            MCP server package (server.py is the entrypoint, -m mcp_server.server)
  config.py              data-file paths, resolved relative to the project root
  historian_tools.py     reads boiler_historian.duckdb
  alarm_tools.py         reads boiler_alarms.duckdb
  kg_tools.py            reads boiler_kg.json
  docs_tools.py          reads rag_vector_db/ (ChromaDB)
  equipment_crosswalk.py maps the RAG docs' equipment vocabulary onto boiler_kg.json node ids
  audit_tools.py         investigation logging (audit.db per investigation)
scripts/
  build_kg.py            regenerates boiler_kg.json from the plant's equipment/stream topology
  render_investigation_report.py   renders report.md/summary.md for the investigation-report skill
build_docs_db.py       rebuilds rag_vector_db/ from RAG docs/
RAG docs/              62 markdown docs: controls/, datasheets/, maintenance/, safety/, sops/, troubleshooting/
boiler_historian.duckdb   86,400 rows x 30 tags, 5-second intervals
boiler_alarms.duckdb      6,005 alarm_events rows
boiler_kg.json            64 nodes / 98 edges, networkx-compatible node-link JSON
investigations/        one subdirectory per investigation (audit.db, report.md, summary.md)
.claude/skills/investigation-report/   the report-rendering skill
.mcp.json              registers the MCP server with Claude Code
```

## Setup

Requires a project-local venv at `.venv/` on Python 3.13 (not newer system Pythons,
which lack `onnxruntime` wheels needed by `chromadb`).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Copy `.mcp.json.example` to `.mcp.json` and set both paths to your clone. The real
`.mcp.json` is gitignored because it holds machine-specific absolute paths. It
registers the server for Claude Code:

```json
{
  "mcpServers": {
    "plant-troubleshooting": {
      "command": "<project>/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "<project>"
    }
  }
}
```

Run the server directly for debugging: `.venv/bin/python -m mcp_server.server`.

**After creating or editing `.mcp.json`, fully restart the Claude Code terminal**
(quit and relaunch — not just starting a new session) before the new or changed MCP
server will be picked up.

To rebuild derived data after editing sources:
- Knowledge graph: `.venv/bin/python scripts/build_kg.py` (reads sensor ranges live
  from `boiler_historian.duckdb`, so the two never drift apart)
- Docs vector DB: `.venv/bin/python build_docs_db.py` (incremental) or `--rebuild`
  (wipe and start over)

## How this was built

This project was built inside Claude Code almost entirely through a handful of
natural-language prompts, in this order. They're included here as a reference for
anyone picking up this project who wants to reproduce the approach, extend it, or
retarget it at a different dataset.

**1. Historian** — turn raw time-series + tag-description files into a queryable
mock historian:

> I've included a handful of documents for a time series dataset for a coal fired
> boiler. Please convert the time series data (titled "data") and the tag
> descriptions (titled "columns") into a duck db that will act as a mock historian
> we can query

**2. Knowledge graph** — map process connectivity from a P&ID/PFD, the source
research paper, and the tag descriptions:

> Now I'd like to create a knowledge graph that maps out the process connectivity
> in detail: what equipment feeds what, including any valves and piping
> components; what sensors measure which values; how the control loops work. Use
> the attached PFD, research paper, and tag descriptions to create the graph.
> Please follow knowledge graph best practices when building

**3. Document library / RAG** — turn a folder of plant documentation into a
semantically searchable vector database:

> There's a folder titled "RAG docs" that contains a bunch of plant documentation.
> I'd like to create a vector database so we can add a RAG component to the
> application. our troubleshooting assistant will be able to do a semantic search
> of the vector database to help troubleshoot issues

**4. MCP server + audit trail** — tie the four data sources together behind an MCP
server and add real-time investigation logging:

> The project folder includes a time series database, a database of alarm logs, a
> knowledge graph, and a vector database of plant documentation. My goal is to
> create a troubleshooting assistant where users type in natural language prompts
> into claude code and the assistant drills down to the root cause of the issue
> using the time series data, alarm logs, knowledge graph, and vector database.
> It's important that the assistant can distinguish between root cause and
> symptoms. We will stand up an MCP server with a set of deterministic tools that
> allows claude code to retrieve data from the historian, alarm logs, knowledge
> graph, and vector database. Create whatever tools we'll need to retrieve the
> relevant data from these databases. We will also create a feature that allows
> the assistant to log all steps it takes to solve the problem (every tool call,
> hypothesis, reasoning step, etc.) as it executes in real time (not from memory).
> These steps should be outputted in a report for an SME to review. In addition to
> this report, a summary document will also be outputted that contains the
> following: root cause of issue, approximate confidence level, causal chain,
> recommended actions to fix the issues

Each prompt was given its own supporting source files in context (the raw
time-series export and column descriptions for #1; a PFD image and the source
research paper for #2; the `RAG docs/` folder for #3), which Claude Code read and
used to produce the corresponding `.duckdb`/`.json`/vector-store artifact and the
`mcp_server/` tools and `audit_tools.py` logging described above.

## Porting this to a new plant

**This is not multi-tenant, and it's not a config change.** `mcp_server/config.py`
resolves all four data paths relative to the project folder
(`boiler_historian.duckdb`, `boiler_alarms.duckdb`, `boiler_kg.json`,
`rag_vector_db/`) — there's no environment variable or plant-id switch. More
importantly, several pieces are hand-built for *this* CFB boiler, not derived
automatically from data:

- **`scripts/build_kg.py`** encodes this plant's actual equipment topology — which
  sensor sits on which piece of equipment, which streams connect what. A new plant
  needs its own version of this script.
- **`mcp_server/equipment_crosswalk.py`** is a hand-written mapping between the
  knowledge graph's node ids and the RAG docs' `equipment:` frontmatter vocabulary.
  It's specific to this plant's two id vocabularies.
- **`RAG docs/`** is 62 markdown docs (SOPs, datasheets, troubleshooting guides)
  someone wrote for this unit. There's no new-plant equivalent until someone writes
  them.

### Steps to port

1. **Copy the project folder** for the new plant, with its own `.venv` and its own
   `.mcp.json` entry under a distinct server name. Because `config.py` resolves
   paths relative to the package location, a copy is self-contained — no code
   changes needed just for paths.
2. **Historian** — build a `boiler_historian.duckdb` with:
   - `historian_data`: `timestamp` + one `DOUBLE` column per tag
   - `tags`: one row per tag — `tag_name, description, source_description, units,
     normal_min, normal_max, range_source_min, range_source_max`
3. **Alarms** — build a `boiler_alarms.duckdb` with a single `alarm_events` table:
   `alarm_id, tag_name, alarm_level, priority, message, setpoint, activated_at,
   activated_value, acknowledged_at, cleared_at, duration_sec, max_deviation`.
4. **Knowledge graph** — write a new-plant version of `scripts/build_kg.py` (its own
   node/edge list for that plant's equipment and process streams) and regenerate
   `boiler_kg.json`.
5. **Docs** — write the new plant's own markdown library under `RAG docs/`
   (`controls/`, `datasheets/`, `maintenance/`, `safety/`, `sops/`,
   `troubleshooting/`), then run `build_docs_db.py --rebuild` to get a fresh
   `rag_vector_db/`.
6. **Crosswalk** — rewrite `equipment_crosswalk.py`'s mapping for the new plant's KG
   node ids and docs equipment ids.
7. **CLAUDE.md** — write a new-plant version documenting the schema and any data
   quirks (null-bound semantics, doc_type singulars, etc.) the way the current one
   does — the tool code and skills lean on that context for correct usage.

Steps 2–3 depend entirely on what format the new plant's historian/alarm exports
actually come in; that's real data-engineering work, not a mechanical conversion.

## Data and attribution

The boiler sensor data underlying `boiler_historian.duckdb` comes from:

> Hu, W., Jiang, A., Chen, K., Zheng, J., Shang, W., & Cao, Z. (2025). A long-tailed
> distribution time-series dataset in boiler equipment. *Scientific Data*.
> https://doi.org/10.1038/s41597-025-05096-4

`41597_2025_5096_Fig3_HTML.png` is Figure 3 from that paper, reproduced here under its
open-access license and used as the process-topology source for `scripts/build_kg.py`.

Everything else is synthetic or hand-written for this project: the 6,005-row alarm log,
the 64-node knowledge graph, and the 62 documents in `RAG docs/` (SOPs, datasheets,
troubleshooting guides) do not come from the source paper and do not describe a real
plant's procedures. They should not be used as operational guidance.

## License

MIT, see [LICENSE](LICENSE). The license covers the code in this repository, not the
third-party dataset and figure described above, which carry their own terms.
