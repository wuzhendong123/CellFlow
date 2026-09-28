"""元数据库表定义（TECH_DESIGN §3）。

所有表不使用数据库外键约束，引用完整性由应用层保证。
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import DATETIME, TINYINT

metadata = MetaData()


def _ts(name: str, nullable: bool = False, onupdate: bool = False) -> Column:
    kwargs = {"nullable": nullable}
    if not nullable:
        kwargs["server_default"] = func.current_timestamp(3)
    if onupdate:
        kwargs["server_onupdate"] = func.current_timestamp(3)
    return Column(name, DATETIME(fsp=3).with_variant(DateTime(), "sqlite"), **kwargs)


def _bool(name: str, default: int) -> Column:
    return Column(name, TINYINT(1).with_variant(Integer(), "sqlite"), nullable=False, server_default=str(default))


_opts = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# ========== 设计期 ==========
pipeline = Table(
    "cf_pipeline", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False),
    Column("name", String(128), nullable=False),
    Column("datasource_id", BigInteger, nullable=False),
    Column("published_rev_id", BigInteger),
    Column("sample_file_id", BigInteger),
    _bool("frozen", 0),
    Column("description", String(512)),
    Column("owner", String(64), nullable=False),
    _ts("created_at"),
    _ts("updated_at", onupdate=True),
    UniqueConstraint("code", name="uk_code"),
    **_opts,
)

pipeline_draft = Table(
    "cf_pipeline_draft", metadata,
    Column("pipeline_id", BigInteger, primary_key=True, autoincrement=False),
    Column("dsl", JSON, nullable=False),
    Column("base_rev", Integer),
    Column("version", BigInteger, nullable=False),
    Column("updated_by", String(64), nullable=False),
    _ts("updated_at", onupdate=True),
    **_opts,
)

pipeline_revision = Table(
    "cf_pipeline_revision", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("pipeline_id", BigInteger, nullable=False),
    Column("rev", Integer, nullable=False),
    Column("dsl", JSON, nullable=False),
    Column("dsl_schema_ver", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("note", String(512)),
    Column("regression_report", JSON),
    Column("created_by", String(64), nullable=False),
    Column("published_by", String(64)),
    _ts("published_at", nullable=True),
    UniqueConstraint("pipeline_id", "rev", name="uk_rev"),
    **_opts,
)

datasource = Table(
    "cf_datasource", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", String(64), nullable=False),
    Column("conn_mode", String(8), nullable=False, server_default="REF"),  # REF：引用环境变量；DIRECT：控制台直接填写
    Column("host_ref", String(128)),
    Column("db_name", String(64), nullable=False),
    Column("credential_ref", String(128)),
    Column("host", String(255)),
    Column("port", Integer),
    Column("username", String(128)),
    Column("password_enc", Text),  # CF_SECRET_KEY 加密后的口令，接口不返回
    UniqueConstraint("name", name="uk_name"),
    **_opts,
)

table_binding = Table(
    "cf_table_binding", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("revision_id", BigInteger, nullable=False),
    Column("dataset", String(128), nullable=False),
    Column("table_name", String(64), nullable=False),
    Column("strategy", String(16), nullable=False),
    Column("key_fields", JSON),
    Column("column_mapping", JSON, nullable=False),
    Column("guards", JSON),
    UniqueConstraint("revision_id", "dataset", name="uk_ds"),
    UniqueConstraint("revision_id", "table_name", name="uk_tbl"),
    **_opts,
)

table_owner = Table(
    "cf_table_owner", metadata,
    Column("datasource_id", BigInteger, primary_key=True, autoincrement=False),
    Column("table_name", String(64), primary_key=True),
    Column("pipeline_id", BigInteger, nullable=False),
    **_opts,
)

client_app = Table(
    "cf_client_app", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("app_key", String(64), nullable=False),
    Column("secret_ref", String(128), nullable=False),
    Column("name", String(128), nullable=False),
    Column("allowed_pipelines", JSON, nullable=False),
    Column("callback_allowlist", JSON, nullable=False),
    Column("rate_limit_per_min", Integer, nullable=False),
    _bool("enabled", 1),
    UniqueConstraint("app_key", name="uk_key"),
    **_opts,
)

# ========== 运行期 ==========
source_file = Table(
    "cf_source_file", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("sha256", String(64), nullable=False),
    Column("storage_uri", String(512), nullable=False),
    Column("file_name", String(255), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sheet_meta", JSON, nullable=False),
    Column("uploaded_by", String(64), nullable=False),
    _ts("uploaded_at"),
    Index("idx_sha", "sha256"),
    **_opts,
)

parse_job = Table(
    "cf_parse_job", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("pipeline_id", BigInteger, nullable=False),
    Column("revision_id", BigInteger),
    Column("dsl_snapshot", JSON),
    Column("client_app_id", BigInteger),
    Column("idempotency_key", String(128)),
    Column("file_id", BigInteger, nullable=False),
    Column("mode", String(16), nullable=False),
    Column("status", String(24), nullable=False),
    Column("superseded_by", BigInteger),
    Column("operator", String(64)),
    Column("callback_url", String(512)),
    Column("error_summary", JSON),
    Column("metrics", JSON),
    Column("result", JSON),
    Column("release_id", BigInteger),
    _ts("submitted_at"),
    _ts("started_at", nullable=True),
    _ts("finished_at", nullable=True),
    UniqueConstraint("client_app_id", "idempotency_key", name="uk_idem"),
    Index("idx_pipe_status", "pipeline_id", "status", "id"),
    **_opts,
)

job_issue = Table(
    "cf_job_issue", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("job_id", BigInteger, nullable=False),
    Column("node_id", String(64), nullable=False),
    Column("rule_id", String(64)),
    Column("severity", String(8), nullable=False),
    Column("code", String(32), nullable=False),
    Column("row_id", String(64)),
    Column("sheet", String(128)),
    Column("cell", String(16)),
    Column("field", String(128)),
    Column("value_text", String(1024)),
    Column("message", String(1024), nullable=False),
    Column("related_cells", JSON),
    Index("idx_job", "job_id", "node_id"),
    **_opts,
)

snapshot = Table(
    "cf_snapshot", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("job_id", BigInteger),
    Column("dataset", String(128), nullable=False),
    Column("storage_uri", String(512), nullable=False),
    Column("schema_json", JSON, nullable=False),
    Column("key_fields", JSON),
    Column("row_count", Integer, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Index("idx_job_ds", "job_id", "dataset"),
    **_opts,
)

# ========== 发布与回滚 ==========
release = Table(
    "cf_release", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("pipeline_id", BigInteger, nullable=False),
    Column("job_id", BigInteger),
    Column("kind", String(16), nullable=False),
    Column("rollback_to", BigInteger),
    Column("prev_release_id", BigInteger),
    Column("status", String(16), nullable=False),
    Column("write_plan", JSON, nullable=False),
    Column("table_checksums", JSON),
    Column("change_summary", JSON),
    Column("change_uri", String(512)),
    Column("operator", String(64), nullable=False),
    Column("reason", String(512)),
    _ts("created_at"),
    _ts("published_at", nullable=True),
    Index("idx_pipe", "pipeline_id", "id"),
    **_opts,
)

release_snapshot = Table(
    "cf_release_snapshot", metadata,
    Column("release_id", BigInteger, primary_key=True, autoincrement=False),
    Column("dataset", String(128), primary_key=True),
    Column("snapshot_id", BigInteger, nullable=False),
    Column("table_name", String(64), nullable=False),
    **_opts,
)

live_state = Table(
    "cf_live_state", metadata,
    Column("pipeline_id", BigInteger, primary_key=True, autoincrement=False),
    Column("release_id", BigInteger, nullable=False),
    Column("version", BigInteger, nullable=False),
    _ts("updated_at", onupdate=True),
    **_opts,
)

outbox = Table(
    "cf_outbox", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("release_id", BigInteger),
    Column("job_id", BigInteger),
    Column("channel", String(16), nullable=False),
    Column("target", String(512), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(16), nullable=False, server_default="PENDING"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", String(512)),
    _ts("next_retry_at", nullable=True),
    Index("idx_status", "status", "next_retry_at"),
    **_opts,
)

# ========== 系统设置与审计 ==========
system_setting = Table(
    "cf_system_setting", metadata,
    Column("setting_key", String(64), primary_key=True),
    Column("value_json", JSON, nullable=False),
    Column("updated_by", String(64), nullable=False),
    _ts("updated_at", onupdate=True),
    **_opts,
)

audit_log = Table(
    "cf_audit_log", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("operator", String(64), nullable=False),
    Column("source_ip", String(45), nullable=False),
    Column("action", String(32), nullable=False),
    Column("target", String(128), nullable=False),
    Column("detail", JSON),
    Column("reason", String(512)),
    _ts("created_at"),
    Index("idx_target", "target", "id"),
    **_opts,
)
