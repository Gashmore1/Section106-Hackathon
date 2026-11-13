# Graph Report - docs  (2026-11-13)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 189 nodes · 407 edges · 10 communities (6 shown, 4 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 23 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6db18cc0`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5

## God Nodes (most connected - your core abstractions)
1. `run_deterministic_audit()` - 21 edges
2. `extract_s106_obligations_vllm()` - 16 edges
3. `get_sheffield_ward_summaries()` - 14 edges
4. `_build_fallback_extraction()` - 13 edges
5. `stream_audit_workflow()` - 13 edges
6. `BoundingBox` - 12 edges
7. `ParsedTextBlock` - 12 edges
8. `build_s106_langgraph()` - 12 edges
9. `ExtractedAgreement` - 11 edges
10. `IndexationMethod` - 11 edges

## Surprising Connections (you probably didn't know these)
- `S106AuditState` --uses--> `AuditReport`  [INFERRED]
  agents/workflow.py → shared/schemas.py
- `get_sheffield_ward_summaries()` --uses--> `AuditReport`  [INFERRED]
  data/sql_tools.py → shared/schemas.py
- `run_deterministic_audit()` --uses--> `AuditStatus`  [INFERRED]
  data/sql_tools.py → shared/schemas.py
- `find_quote_bbox()` --uses--> `BoundingBox`  [INFERRED]
  agents/pdf_parser.py → shared/schemas.py
- `ParsedTextBlock` --uses--> `BoundingBox`  [INFERRED]
  agents/pdf_parser.py → shared/schemas.py

## Import Cycles
- None detected.

## Communities (10 total, 4 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.10
Nodes (17): _build_fallback_extraction(), AuditDiscrepancy, AuditReport, AuditStatus, BoundingBox, ClauseEvidence, ContributionCategory, DeveloperAgreementContributionCSVRow (+9 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (16): build_s106_langgraph(), node_postgis_spatial_checker(), node_sql_indexation_auditor(), audit_sse_stream(), event_generator(), export_demand_notice_docx(), export_statutory_csv(), get_audit_report() (+8 more)

### Community 2 - "Community 2"
Cohesion: 0.12
Nodes (11): Base, get_engine(), InflationIndexRecord, S106AgreementRecord, S106ObligationRecord, seed_database(), SheffieldWardRecord, compute_indexed_obligation() (+3 more)

### Community 3 - "Community 3"
Cohesion: 0.12
Nodes (9): find_quote_bbox(), format_blocks_for_llm(), parse_s106_pdf(), ParsedTextBlock, node_pdf_parser(), _make_event(), S106AuditState, stream_audit_workflow() (+1 more)

### Community 4 - "Community 4"
Cohesion: 0.12
Nodes (7): _call_ollama_api_chat(), _call_vllm_openai(), extract_s106_obligations_vllm(), _is_ollama_endpoint(), _ollama_root_url(), node_vllm_extractor(), run_five_input_benchmark()

### Community 5 - "Community 5"
Cohesion: 0.10
Nodes (20): context, build, container_name, environment, healthcheck, image, ports, restart (+12 more)

## Knowledge Gaps
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_deterministic_audit()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `extract_s106_obligations_vllm()` (e.g. with `ParsedTextBlock` and `ExtractedAgreement`) actually correct?**
  _`extract_s106_obligations_vllm()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._
- **Why does `get_sheffield_ward_summaries()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `stream_audit_workflow()` (e.g. with `ParsedTextBlock` and `AgentTelemetryEvent`) actually correct?**
  _`stream_audit_workflow()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.08392603129445235 - nodes in this community are weakly interconnected._
- **Why does `extract_s106_obligations_vllm()` connect `Community 4` to `Community 0`, `Community 1`, `Community 3`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._