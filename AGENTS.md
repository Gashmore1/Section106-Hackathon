# Project Context: Sheffield Section 106 & Infrastructure Levy Autonomous Auditor

**Hackathon Track:** Economic Systems (NVIDIA DGX Spark Local Compute Hackathon)

## 1. System Overview & Core Value Proposition
We are building an air-gapped, local multi-agent legal and financial auditing pipeline running entirely on an **NVIDIA DGX Spark (Grace Blackwell GB10, ARM64, 128GB unified memory)**.

- **Problem:** UK Local Authorities (specifically Sheffield City Council) secure millions in developer contributions via Section 106 (S106) legal agreements. These obligations are locked inside 40–60 page unstructured PDFs with complex triggers (e.g., *"prior to occupation of 50th dwelling"*) and inflation indexation clauses (RPI/CPI/BCIS). Additionally, Sheffield currently has **0/3** statutory Developer Contribution CSV endpoints live on `planning.data.gov.uk` (`developer-agreement`, `developer-agreement-contribution`, `developer-agreement-transaction`).
- **Why Local Compute (DGX Spark):** Pre-decision financial viability assessments and developer deeds contain commercially embargoed data and PII that cannot be sent to external cloud APIs. Everything runs 100% locally via **vLLM** and **PostgreSQL**.

---

## 2. Tech Stack & Environment Constraints
- **Hardware Architecture:** `linux/arm64` (NVIDIA Grace CPU + Blackwell GB10 GPU, 128GB unified memory). **Never use `amd64`-only Docker images.**
- **Local LLM Server:** Ollama (`http://localhost:11434/v1`) or vLLM (`http://localhost:8000/v1`) serving preloaded DGX Spark models (`qwen3.6:35b-a3b`, `nemotron-3.5-lightning:30b`, `nemotron-3-nano:30b`) with structured JSON schema outputs (`format=schema` / `response_format` / `guided_json`).
- **Database:** PostgreSQL 16 with `postgis` and `pgvector` extensions (`postgresql+psycopg://spark:sparkpassword@localhost:5432/sheffield_s106`).
- **Backend & Orchestration:** Python 3.11+, FastAPI (with Server-Sent Events for live agent telemetry), LangGraph, Pydantic v2, SQLAlchemy 2.0, PyMuPDF (`fitz`), `python-docx`.
- **Frontend:** Split-screen dashboard (PDF Evidence Viewer + Financial Audit Ledger + Sheffield Ward PostGIS Map).

---

## 3. Repository Structure & Track Ownership

```text
.
├── AGENTS.md               # Agent & team architectural rules and context
├── README.md               # Quickstart, setup, and hackathon runbook
├── requirements.txt        # Python 3.11+ dependencies
├── .env.example            # Local environment defaults for DGX Spark
├── docker-compose.yml      # ARM64-compatible Postgres 16 (PostGIS + pgvector)
├── db/                     # ARM64 PostGIS + pgvector Dockerfile & init scripts
│   ├── Dockerfile
│   └── init/
│       └── 01-extensions.sql
├── shared/
│   ├── __init__.py
│   └── schemas.py          # SINGLE SOURCE OF TRUTH: Do not modify field names without team sync
├── data/                   # Track 1: Postgres schema, seed scripts, deterministic SQL/PostGIS tools
│   ├── __init__.py
│   ├── seed_db.py
│   └── sql_tools.py
├── agents/                 # Track 2: PyMuPDF parser, vLLM structured extractor, LangGraph workflow
│   ├── __init__.py
│   ├── prompts.py
│   ├── pdf_parser.py
│   ├── extractor.py
│   └── workflow.py
└── app/                    # Track 3: FastAPI SSE endpoints, .docx/CSV generators, UI dashboard
    ├── __init__.py
    ├── main.py
    └── frontend/
        └── index.html
```

---

## 4. Engineering Rules & Contracts
1. **Single Source of Truth (`shared/schemas.py`):** All tracks must import Pydantic models and enums from `shared.schemas`. Do not change field names or types without syncing across Track 1 (Data), Track 2 (Agents), and Track 3 (App).
2. **Deterministic Math Only (`data/sql_tools.py`):** LLMs must **never** compute inflation indexation or financial totals directly. The LLM extracts structured parameters (`base_amount_gbp`, `indexation_method`, `index_base_date`, `trigger_threshold`), and deterministic SQL/Python tools compute the indexed amounts using official ONS/BCIS index tables in PostgreSQL.
3. **ARM64 Native:** Ensure all containers and wheels support `linux/arm64` for the NVIDIA Grace Blackwell GB10 architecture.
4. **Evidence Traceability:** Every extracted S106 obligation must carry exact PDF page numbers, bounding boxes (`bbox`), and verbatim clause quotes so the frontend PDF viewer can highlight the legal source.
