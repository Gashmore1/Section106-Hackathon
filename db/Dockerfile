# PostgreSQL 16 + PostGIS 3.4 + pgvector, ARM64 (DGX Spark GB10)
# Base is PGDG-based (Debian bookworm), so postgresql-16-pgvector installs cleanly.
FROM imresamu/postgis-arm64:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
