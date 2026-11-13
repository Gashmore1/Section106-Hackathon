# Graph Report - docs  (2026-11-13)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 279 nodes · 584 edges · 12 communities (8 shown, 4 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 14 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6bad0cee`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7

## God Nodes (most connected - your core abstractions)
1. `run_deterministic_audit()` - 24 edges
2. `extract_s106_obligations_vllm()` - 17 edges
3. `ParsedTextBlock` - 15 edges
4. `stream_audit_workflow()` - 15 edges
5. `ExtractedAgreement` - 14 edges
6. `_build_fallback_extraction()` - 14 edges
7. `get_sheffield_ward_summaries()` - 14 edges
8. `build_s106_langgraph()` - 13 edges
9. `BoundingBox` - 12 edges
10. `parse_s106_pdf()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `node_postgis_spatial_checker()` --calls--> `get_sheffield_ward_summaries()`  [EXTRACTED]
  agents/workflow.py → data/sql_tools.py
- `event_generator()` --calls--> `stream_audit_workflow()`  [EXTRACTED]
  app/main.py → agents/workflow.py
- `_build_fallback_extraction()` --calls--> `BoundingBox`  [EXTRACTED]
  agents/extractor.py → shared/schemas.py
- `run_deterministic_audit()` --calls--> `AuditDiscrepancy`  [EXTRACTED]
  data/sql_tools.py → shared/schemas.py
- `run_deterministic_audit()` --calls--> `AuditReport`  [EXTRACTED]
  data/sql_tools.py → shared/schemas.py

## Import Cycles
- None detected.

## Communities (12 total, 4 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (22): _attach_pdf_bboxes(), extract_s106_obligations_vllm(), filter_relevant_s106_blocks(), find_quote_bbox(), format_blocks_for_llm(), _parse_pdf_stream_fallback(), parse_s106_pdf(), ParsedTextBlock (+14 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (15): load_inflation_indices(), load_sheffield_wards(), download_ons_inflation_series(), download_planning_data_csvs(), download_real_s106_pdfs(), download_sheffield_spatial_datasets(), _http_get(), main() (+7 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (17): _build_fallback_extraction(), generate_statutory_csv_rows(), AuditDiscrepancy, AuditReport, AuditStatus, ClauseEvidence, ContributionCategory, DeveloperAgreementContributionCSVRow (+9 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (38): build, container_name, depends_on, environment, healthcheck, image, ports, restart (+30 more)

### Community 4 - "Community 4"
Cohesion: 0.10
Nodes (14): Base, get_engine(), InflationIndexRecord, S106AgreementRecord, S106ObligationRecord, seed_database(), SheffieldWardRecord, compute_indexed_obligation() (+6 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (12): audit_sse_stream(), event_generator(), endpoint_publication_benchmark(), export_demand_notice_docx(), export_statutory_csv(), get_audit_report(), health_check(), list_agreements() (+4 more)

### Community 6 - "Community 6"
Cohesion: 0.15
Nodes (4): _call_ollama_api_chat(), _call_vllm_openai(), _is_ollama_endpoint(), _ollama_root_url()

### Community 7 - "Community 7"
Cohesion: 0.22
Nodes (8): DATABASE_URL, LLM_KEEP_ALIVE, LLM_NUM_CTX, LLM_NUM_PREDICT, run_spark.sh script, VLLM_API_KEY, VLLM_BASE_URL, VLLM_MODEL_NAME

## Knowledge Gaps
- **7 isolated node(s):** `DATABASE_URL`, `LLM_KEEP_ALIVE`, `LLM_NUM_CTX`, `LLM_NUM_PREDICT`, `VLLM_API_KEY` (+2 more)
  These have ≤1 connection - possible missing edges. (Counts symbols only; 140 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_deterministic_audit()` connect `Community 0` to `Community 2`, `Community 4`, `Community 5`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `ParsedTextBlock` (e.g. with `_attach_pdf_bboxes()` and `extract_s106_obligations_vllm()`) actually correct?**
  _`ParsedTextBlock` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `DATABASE_URL`, `LLM_KEEP_ALIVE`, `LLM_NUM_CTX` to the rest of the system?**
  _7 weakly-connected nodes found - possible missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.08078431372549019 - nodes in this community are weakly interconnected._
- **Why does `get_sheffield_ward_summaries()` connect `Community 4` to `Community 0`, `Community 2`, `Community 5`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.07777777777777778 - nodes in this community are weakly interconnected._
- **Why does `stream_audit_workflow()` connect `Community 0` to `Community 4`, `Community 5`, `Community 6`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._