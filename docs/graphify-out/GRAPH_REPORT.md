# Graph Report - docs  (2026-11-13)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 172 nodes · 371 edges · 11 communities (6 shown, 5 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 25 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c5ae1f23`
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

## God Nodes (most connected - your core abstractions)
1. `run_deterministic_audit()` - 21 edges
2. `get_sheffield_ward_summaries()` - 15 edges
3. `_build_fallback_extraction()` - 13 edges
4. `ParsedTextBlock` - 12 edges
5. `BoundingBox` - 12 edges
6. `build_s106_langgraph()` - 12 edges
7. `stream_audit_workflow()` - 12 edges
8. `IndexationMethod` - 11 edges
9. `extract_s106_obligations_vllm()` - 11 edges
10. `fetch_all_agreements()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `ParsedTextBlock` --uses--> `BoundingBox`  [INFERRED]
  agents/pdf_parser.py → shared/schemas.py
- `S106AuditState` --uses--> `AuditReport`  [INFERRED]
  agents/workflow.py → shared/schemas.py
- `S106AuditState` --uses--> `ExtractedAgreement`  [INFERRED]
  agents/workflow.py → shared/schemas.py
- `get_sheffield_ward_summaries()` --uses--> `AuditReport`  [INFERRED]
  data/sql_tools.py → shared/schemas.py
- `find_quote_bbox()` --uses--> `BoundingBox`  [INFERRED]
  agents/pdf_parser.py → shared/schemas.py

## Import Cycles
- None detected.

## Communities (11 total, 5 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.11
Nodes (13): Base, get_engine(), InflationIndexRecord, S106AgreementRecord, S106ObligationRecord, seed_database(), SheffieldWardRecord, fetch_all_agreements() (+5 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (13): extract_s106_obligations_vllm(), parse_s106_pdf(), ParsedTextBlock, build_s106_langgraph(), node_pdf_parser(), node_postgis_spatial_checker(), node_vllm_extractor(), _make_event() (+5 more)

### Community 2 - "Community 2"
Cohesion: 0.11
Nodes (9): audit_sse_stream(), event_generator(), export_demand_notice_docx(), export_statutory_csv(), get_audit_report(), health_check(), list_agreements(), list_ward_summaries() (+1 more)

### Community 3 - "Community 3"
Cohesion: 0.15
Nodes (9): _build_fallback_extraction(), AuditDiscrepancy, AuditReport, BoundingBox, ClauseEvidence, ExtractedAgreement, ExtractedObligation, IndexationCalculationResult (+1 more)

### Community 4 - "Community 4"
Cohesion: 0.10
Nodes (20): context, build, container_name, environment, healthcheck, image, ports, restart (+12 more)

### Community 5 - "Community 5"
Cohesion: 0.16
Nodes (8): node_sql_indexation_auditor(), compute_indexed_obligation(), _lookup_index_value(), run_deterministic_audit(), AuditStatus, ContributionCategory, IndexationMethod, TriggerType

## Knowledge Gaps
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_deterministic_audit()` connect `Community 5` to `Community 0`, `Community 1`, `Community 2`, `Community 3`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `get_sheffield_ward_summaries()` (e.g. with `SheffieldWardRecord` and `AuditReport`) actually correct?**
  _`get_sheffield_ward_summaries()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.11174242424242424 - nodes in this community are weakly interconnected._
- **Why does `get_sheffield_ward_summaries()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 5`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `ParsedTextBlock` (e.g. with `extract_s106_obligations_vllm()` and `BoundingBox`) actually correct?**
  _`ParsedTextBlock` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.12315270935960591 - nodes in this community are weakly interconnected._
- **Why does `build_s106_langgraph()` connect `Community 1` to `Community 5`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._