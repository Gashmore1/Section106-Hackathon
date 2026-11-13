# Sheffield Section 106 & Infrastructure Levy Autonomous Auditor

**Hackathon Track:** Economic Systems (NVIDIA DGX Spark Local Compute Hackathon)

An air-gapped, local multi-agent legal and financial auditing pipeline running 100% locally on **NVIDIA DGX Spark (Grace Blackwell GB10, ARM64, 128GB unified memory)**.

## Overview

UK Local Authorities (such as Sheffield City Council) secure millions of pounds in developer contributions via Section 106 (S106) legal agreements. These obligations are locked inside 40–60 page unstructured PDF deeds with complex triggers (e.g., *"prior to occupation of the 50th dwelling"*) and inflation indexation clauses (`RPI`, `CPI`, `BCIS`). Furthermore, Sheffield currently has **0/3** statutory Developer Contribution CSV endpoints live on `planning.data.gov.uk`.

Because pre-decision financial viability assessments and deeds contain commercially sensitive data and PII, this system runs completely offline using **vLLM** (`http://localhost:8000/v1`) and **PostgreSQL 16 + PostGIS + pgvector**.

## Repository Structure

```text
s106-auditor/
├── AGENTS.md               # Project context, hardware constraints & track contracts
├── docker-compose.yml      # ARM64-ready PostgreSQL 16 (PostGIS + pgvector)
├── requirements.txt        # Python 3.11+ dependencies
├── .env.example            # Environment variable template
├── shared/
│   └── schemas.py          # SINGLE SOURCE OF TRUTH: Pydantic v2 contracts
├── data/                   # Track 1: Postgres schema, seed scripts, deterministic SQL/PostGIS tools
│   ├── seed_db.py
│   └── sql_tools.py
├── agents/                 # Track 2: PyMuPDF parser, vLLM structured extractor, LangGraph workflow
│   ├── prompts.py
│   ├── pdf_parser.py
│   ├── extractor.py
│   └── workflow.py
└── app/                    # Track 3: FastAPI SSE endpoints, .docx/CSV generators, UI dashboard
    ├── main.py
    └── frontend/
        └── index.html
```

## Quickstart (DGX Spark `linux/arm64`)

### 1. Start PostgreSQL (PostGIS + pgvector)
```bash
docker compose up -d --build
```

### 2. Install Python Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Seed the Sheffield S106 Database
```bash
python -m data.seed_db
```

### 4. Start the FastAPI Backend & Dashboard
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```
Open `http://localhost:8080` to view the Split-Screen Auditor Dashboard (PDF Evidence Viewer + Financial Audit Ledger + Sheffield Ward PostGIS Map).
