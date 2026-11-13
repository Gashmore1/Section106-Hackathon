-- Runs automatically on first container initialisation (docker-entrypoint-initdb.d).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
