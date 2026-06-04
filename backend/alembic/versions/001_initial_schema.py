"""Initial schema and platform seed."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sync_status_enum = postgresql.ENUM(
    "pending", "ok", "error", "unchanged", name="sync_status_enum", create_type=False
)
platform_link_sync_status_enum = postgresql.ENUM(
    "idle", "syncing", "ok", "error", name="platform_link_sync_status_enum", create_type=False
)
sync_run_status_enum = postgresql.ENUM(
    "queued", "running", "success", "failed", "partial", name="sync_run_status_enum", create_type=False
)
sync_job_status_enum = postgresql.ENUM(
    "queued", "running", "success", "failed", name="sync_job_status_enum", create_type=False
)
sync_job_trigger_enum = postgresql.ENUM(
    "login", "manual", "linking", name="sync_job_trigger_enum", create_type=False
)
auth_login_request_status_enum = postgresql.ENUM(
    "pending", "completed", "expired", name="auth_login_request_status_enum", create_type=False
)
sync_log_level_enum = postgresql.ENUM(
    "info", "warning", "error", "debug", name="sync_log_level_enum", create_type=False
)

FIVE_VERST_CAPABILITIES = {
    "validate_profile_url": True,
    "fetch_profile_preview": True,
    "fetch_user_profile": True,
    "fetch_user_runs": False,
    "fetch_user_volunteering": False,
    "fetch_locations": True,
    "fetch_events": True,
    "fetch_event_results": True,
    "fetch_event_volunteers": True,
}

S95_CAPABILITIES = {
    "validate_profile_url": True,
    "fetch_profile_preview": True,
    "fetch_user_profile": True,
    "fetch_user_runs": False,
    "fetch_user_volunteering": False,
    "fetch_locations": True,
    "fetch_events": True,
    "fetch_event_results": True,
    "fetch_event_volunteers": True,
}

PARKRUN_CAPABILITIES = {
    "validate_profile_url": True,
    "fetch_profile_preview": True,
    "fetch_user_profile": True,
    "fetch_user_runs": True,
    "fetch_user_volunteering": False,
    "fetch_locations": False,
    "fetch_events": False,
    "fetch_event_results": False,
    "fetch_event_volunteers": False,
}


def upgrade() -> None:
    sync_status_enum.create(op.get_bind(), checkfirst=True)
    platform_link_sync_status_enum.create(op.get_bind(), checkfirst=True)
    sync_run_status_enum.create(op.get_bind(), checkfirst=True)
    sync_job_status_enum.create(op.get_bind(), checkfirst=True)
    sync_job_trigger_enum.create(op.get_bind(), checkfirst=True)
    auth_login_request_status_enum.create(op.get_bind(), checkfirst=True)
    sync_log_level_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "platforms",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_key", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("country", sa.String(length=128), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sync_status_enum, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_key", name="uq_locations_platform_external_key"),
    )

    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("profile_url", sa.String(length=1024), nullable=True),
        sa.Column("age_category", sa.String(length=64), nullable=True),
        sa.Column("club_name", sa.String(length=256), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sync_status_enum, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_user_id", name="uq_participants_platform_external_user_id"),
    )
    op.create_index("ix_participants_platform_external_user_id", "participants", ["platform_id", "external_user_id"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=128), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("consent_accepted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("consent_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("news_subscribed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_event_key", sa.String(length=512), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("runners_count", sa.Integer(), nullable=True),
        sa.Column("finishers_count", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sync_status_enum, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_event_key", name="uq_events_platform_external_key"),
    )
    op.create_index("ix_events_platform_event_date", "events", ["platform_id", "event_date"])
    op.create_index("ix_events_location_event_date", "events", ["location_id", "event_date"])

    op.create_table(
        "auth_login_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("request_token", sa.String(length=128), nullable=False),
        sa.Column("status", auth_login_request_status_enum, server_default="pending", nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_token"),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_type", sa.String(length=64), nullable=False),
        sa.Column("status", sync_run_status_enum, server_default="queued", nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("records_fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_upserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_platform_started_at", "sync_runs", ["platform_id", "started_at"])

    op.create_table(
        "platform_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_user_id", sa.String(length=128), nullable=False),
        sa.Column("external_url", sa.String(length=1024), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_user_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", platform_link_sync_status_enum, server_default="idle", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_user_id", name="uq_platform_links_platform_external_user_id"),
        sa.UniqueConstraint("user_id", "platform_id", name="uq_platform_links_user_platform"),
    )
    op.create_index("ix_platform_links_user_id", "platform_links", ["user_id"])

    op.create_table(
        "run_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_result_key", sa.String(length=512), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("finish_time_sec", sa.Integer(), nullable=True),
        sa.Column("finish_time_display", sa.String(length=32), nullable=True),
        sa.Column("pace_sec_per_km", sa.Integer(), nullable=True),
        sa.Column("pace_display", sa.String(length=16), nullable=True),
        sa.Column("age_category", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("is_pr", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "external_result_key", name="uq_run_results_event_external_key"),
    )
    op.create_index("ix_run_results_participant_event", "run_results", ["participant_id", "event_id"])
    op.create_index("ix_run_results_event_position", "run_results", ["event_id", "position"])

    op.create_table(
        "volunteer_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_result_key", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "external_result_key", name="uq_volunteer_results_event_external_key"),
    )
    op.create_index("ix_volunteer_results_participant_event", "volunteer_results", ["participant_id", "event_id"])

    op.create_table(
        "dashboard_cache",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "auth_one_time_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("login_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["login_request_id"], ["auth_login_requests.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", sync_job_trigger_enum, nullable=False),
        sa.Column("status", sync_job_status_enum, server_default="queued", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_link_id"], ["platform_links.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_jobs_user_created_at", "sync_jobs", ["user_id", "created_at"])

    op.create_table(
        "sync_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", sync_log_level_enum, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sync_job_id"], ["sync_jobs.id"]),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    platforms = sa.table(
        "platforms",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("base_url", sa.String),
        sa.column("capabilities", postgresql.JSONB),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        platforms,
        [
            {
                "code": "five_verst",
                "name": "5 вёрст",
                "base_url": "https://5verst.ru",
                "capabilities": FIVE_VERST_CAPABILITIES,
                "is_active": True,
            },
            {
                "code": "s95",
                "name": "с95",
                "base_url": "https://s95.ru",
                "capabilities": S95_CAPABILITIES,
                "is_active": False,
            },
            {
                "code": "parkrun",
                "name": "parkrun",
                "base_url": "https://www.parkrun.org.uk",
                "capabilities": PARKRUN_CAPABILITIES,
                "is_active": False,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("sync_log_entries")
    op.drop_table("sync_jobs")
    op.drop_table("auth_one_time_tokens")
    op.drop_table("dashboard_cache")
    op.drop_table("volunteer_results")
    op.drop_table("run_results")
    op.drop_table("platform_links")
    op.drop_table("sync_runs")
    op.drop_table("auth_login_requests")
    op.drop_table("events")
    op.drop_table("users")
    op.drop_table("participants")
    op.drop_table("locations")
    op.drop_table("platforms")

    sync_log_level_enum.drop(op.get_bind(), checkfirst=True)
    auth_login_request_status_enum.drop(op.get_bind(), checkfirst=True)
    sync_job_trigger_enum.drop(op.get_bind(), checkfirst=True)
    sync_job_status_enum.drop(op.get_bind(), checkfirst=True)
    sync_run_status_enum.drop(op.get_bind(), checkfirst=True)
    platform_link_sync_status_enum.drop(op.get_bind(), checkfirst=True)
    sync_status_enum.drop(op.get_bind(), checkfirst=True)
