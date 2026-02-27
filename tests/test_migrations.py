"""Tests for SQL migration discovery and v1 schema contents."""

from pathlib import Path

import pytest

from scripts.migrate import discover_migrations


def test_discover_migrations_sorts_versions(tmp_path: Path) -> None:
    (tmp_path / "0002_followup.up.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0002_followup.down.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0001_initial.up.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_initial.down.sql").write_text("SELECT 1;", encoding="utf-8")

    migrations = discover_migrations(tmp_path)
    assert [migration.version for migration in migrations] == ["0001", "0002"]
    assert [migration.name for migration in migrations] == ["initial", "followup"]


def test_discover_migrations_requires_up_and_down(tmp_path: Path) -> None:
    (tmp_path / "0001_initial.up.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="both up/down"):
        discover_migrations(tmp_path)


def test_v1_schema_contains_core_tables_and_indexes() -> None:
    schema_path = Path("db/migrations/0001_core_conversation_schema.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    for table_name in ("conversation", "participant", "message", "event"):
        assert f"create table {table_name}" in sql

    assert "references conversation(id)" in sql
    assert "references participant(id)" in sql
    assert "constraint uq_message_turn unique (conversation_id, turn_index)" in sql
    assert "constraint uq_event_sequence unique (conversation_id, seq_no)" in sql
    assert "create index idx_event_conversation_created_at" in sql


def test_v2_schema_contains_snapshot_read_model() -> None:
    schema_path = Path("db/migrations/0002_conversation_snapshot_read_model.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create table conversation_snapshot" in sql
    assert "conversation_id uuid primary key references conversation(id)" in sql
    assert "last_seq_no bigint not null default 0" in sql
    assert "turn_count integer not null default 0" in sql
    assert "create index idx_conversation_snapshot_status" in sql


def test_v3_schema_contains_source_document_storage() -> None:
    schema_path = Path("db/migrations/0003_source_document_storage.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create table source_document" in sql
    assert "checksum_sha256 text not null" in sql
    assert "storage_key text not null" in sql
    assert "constraint uq_source_document_storage_key unique (storage_key)" in sql
    assert "create index idx_source_document_tenant_workspace" in sql


def test_v4_schema_contains_chunk_and_dlq_tables() -> None:
    schema_path = Path("db/migrations/0004_ingestion_processing_and_dlq.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create table source_chunk" in sql
    assert "create table ingestion_dlq" in sql
    assert "constraint uq_source_chunk_order unique (source_document_id, chunk_index)" in sql
    assert "error_type text not null" in sql
    assert "create index idx_ingestion_dlq_source_document_id" in sql


def test_v5_schema_contains_pgvector_embedding_table() -> None:
    schema_path = Path("db/migrations/0005_source_chunk_embedding_pgvector.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create extension if not exists vector" in sql
    assert "create table source_chunk_embedding" in sql
    assert "embedding vector(128) not null" in sql
    assert "constraint uq_source_chunk_embedding unique (source_chunk_id)" in sql
    assert "using ivfflat (embedding vector_cosine_ops)" in sql


def test_v6_schema_contains_topic_and_mapping_tables() -> None:
    schema_path = Path("db/migrations/0006_topic_clustering_and_mapping.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create table topic" in sql
    assert "create table source_chunk_topic" in sql
    assert "centroid vector(128) not null" in sql
    assert "constraint uq_topic_cluster_key unique (source_document_id, cluster_key)" in sql
    assert "constraint chk_source_chunk_topic_link_type check" in sql


def test_v7_schema_contains_scheduler_tables() -> None:
    schema_path = Path("db/migrations/0007_scheduler_automation_templates.up.sql")
    sql = schema_path.read_text(encoding="utf-8").lower()

    assert "create table automation_template" in sql
    assert "create table automation_run" in sql
    assert "constraint uq_automation_template_name unique" in sql
    assert "constraint uq_automation_run_idempotency unique" in sql
    assert "constraint chk_automation_run_status check" in sql
