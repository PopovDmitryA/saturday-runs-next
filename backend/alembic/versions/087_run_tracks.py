"""Треки пробежек: загруженные участником GPX/FIT и ссылки на Garmin

Участник прикладывает к своей пробежке трек — файл с часов или ссылку на
публичную активность Garmin. Здесь лежит нормализованный результат разбора:
геометрия, метрики трассы, класс качества записи и сверка с протоколом.

Треки — чувствительные данные: точки хранятся обрезанными окрестностью старта
(см. run_track_service), а согласие на обработку фиксируется в users.

Revision ID: 087_run_tracks
Revises: 086_admin_resync_requests
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "087_run_tracks"
down_revision = "086_admin_resync_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_tracks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Привязка к пробежке из протокола: может отсутствовать, если трек
        # загружен раньше, чем протокол доехал до нас, или старт не наш.
        sa.Column(
            "run_result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("run_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        # gpx | fit | tcx | garmin_link
        sa.Column("source", sa.String(length=16), nullable=False),
        # Имя файла или id активности Garmin — по нему ловим повторную загрузку.
        sa.Column("source_ref", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        # Дистанция прибора (что показали часы) и наш собственный замер по
        # сглаженной геометрии: расходятся на 1-2%, и это важно видеть обе.
        sa.Column("device_distance_m", sa.Float(), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("elevation_gain_m", sa.Float(), nullable=True),
        sa.Column("elevation_loss_m", sa.Float(), nullable=True),
        sa.Column("min_elevation_m", sa.Float(), nullable=True),
        sa.Column("max_elevation_m", sa.Float(), nullable=True),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column("device_firmware", sa.String(length=64), nullable=True),
        # Есть ли барометр: набор высоты с приборов без альтиметра в расчёт
        # трассы не берём (проверено 09.09.2026, см. Ч33).
        sa.Column("has_barometer", sa.Boolean(), nullable=True),
        sa.Column("point_count", sa.Integer(), nullable=True),
        sa.Column("sample_interval_sec", sa.Float(), nullable=True),
        # A — годится для паспорта трассы, B — только личный разбор,
        # C — запись слишком редкая или рваная.
        sa.Column("quality_class", sa.String(length=1), nullable=True),
        sa.Column("quality", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Геометрия: [[lat, lon, сек от старта, высота|null], ...]
        sa.Column("points", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Насколько время финиша по треку разошлось с протоколом, секунды.
        sa.Column("protocol_delta_sec", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "source", "source_ref", name="uq_run_tracks_user_source_ref"),
    )
    op.create_index("ix_run_tracks_user_started", "run_tracks", ["user_id", "started_at"])
    op.create_index("ix_run_tracks_run_result", "run_tracks", ["run_result_id"])
    op.create_index("ix_run_tracks_location_started", "run_tracks", ["location_id", "started_at"])

    # Согласие на обработку трека: спрашиваем один раз перед первой загрузкой.
    op.add_column("users", sa.Column("track_consent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "track_consent_at")
    op.drop_index("ix_run_tracks_location_started", table_name="run_tracks")
    op.drop_index("ix_run_tracks_run_result", table_name="run_tracks")
    op.drop_index("ix_run_tracks_user_started", table_name="run_tracks")
    op.drop_table("run_tracks")
