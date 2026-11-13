# Code Graph (graphify)

Machine-generated map of this repository — functions, classes, imports, call edges and
DB schema — built with [graphify](https://docs.graphify.com) using local tree-sitter AST
parsing. **No LLM, no API key, nothing leaves your machine.**

## Files here

| File | Purpose |
| --- | --- |
| `graphify-out/graph.html` | Interactive browser view — click nodes, filter, search |
| `graphify-out/GRAPH_REPORT.md` | Highlights: god nodes, communities, surprising edges, suggested questions |
| `graphify-out/graph.json` | The full graph — this is what you query |
| `graphify-out/manifest.json` | Incremental build manifest (freshness tracking) |

## Install (once)

```bash
brew install uv                      # or: pipx install graphifyy
uv tool install "graphifyy[sql]"     # [sql] extra indexes db/init/*.sql
```

## Query it instead of grepping

The graph lives in `docs/`, so pass `--graph` (run from the repo root):

```bash
G=docs/graphify-out/graph.json

graphify query "indexation uplift" --graph $G --budget 300
graphify explain "run_deterministic_audit()" --graph $G
graphify path "stream_audit_workflow()" "compute_indexed_obligation()" --graph $G
graphify god-nodes --top 10 --graph $G
```

## Regenerate after code changes

```bash
graphify extract . --code-only --out docs
graphify cluster-only docs --no-label
```

The report records the commit it was built from; compare with `git rev-parse HEAD` to
check freshness.

## What the graph tells you about this codebase

**God nodes (most connected — read these first):**

1. `run_deterministic_audit()` — 21 edges. The audit engine and the main cross-track bridge
   (`data/sql_tools.py`), called by the workflow, the API and the CSV exporter.
2. `get_sheffield_ward_summaries()` — 15 edges. PostGIS ward rollups, second bridge node.
3. `_build_fallback_extraction()` — 13 edges. The vLLM-offline fallback path.
4. `ParsedTextBlock` / `BoundingBox` — 12 edges each. PDF evidence plumbing.
5. `build_s106_langgraph()` / `stream_audit_workflow()` — 12 edges each. Orchestration.

**Communities map cleanly onto the three tracks:**

| Community | Contents | Track |
| --- | --- | --- |
| 0 | SQLAlchemy records, `get_engine()`, `seed_database()` | Track 1 (data) |
| 5 | `compute_indexed_obligation()`, `_lookup_index_value()`, `run_deterministic_audit()`, enums | Track 1 (deterministic math) |
| 1 | PDF parser, vLLM extractor, LangGraph nodes | Track 2 (agents) |
| 3 | Pydantic contracts: `ExtractedAgreement`, `AuditDiscrepancy`, `ClauseEvidence` | Shared schemas |
| 2 | FastAPI route handlers, SSE stream, exporters | Track 3 (app) |
| 4 | `docker-compose.yml` service definition | Infra |

**No import cycles detected** — the track boundaries are holding.

> Static graph edges are not runtime guarantees. Confirm with the source and tests before
> treating a path as an execution path.
