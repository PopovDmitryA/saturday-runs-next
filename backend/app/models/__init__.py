import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SyncStatus(str, enum.Enum):
    pending = "pending"
    ok = "ok"
    error = "error"
    unchanged = "unchanged"


class PlatformLinkSyncStatus(str, enum.Enum):
    idle = "idle"
    syncing = "syncing"
    ok = "ok"
    error = "error"


class SyncRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    partial = "partial"


class SyncJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class SyncJobTrigger(str, enum.Enum):
    login = "login"
    manual = "manual"
    linking = "linking"


class AuthLoginRequestStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    expired = "expired"


class AuthProvider(str, enum.Enum):
    telegram = "telegram"
    vk = "vk"
    yandex = "yandex"


class SyncLogLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"
    debug = "debug"


class LocationMergeRequestStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class ProfileFetchPendingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class BacklogCardType(str, enum.Enum):
    bug = "bug"
    feature = "feature"


class BacklogCardStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    rejected = "rejected"
    done = "done"


class ProfileFetchPendingReason(str, enum.Enum):
    cooldown = "cooldown"
    ban = "ban"
    timeout = "timeout"
    error = "error"


class ProfileFetchPendingOperation(str, enum.Enum):
    profile_preview = "profile_preview"
    activity_import = "activity_import"


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    locations: Mapped[list["Location"]] = relationship(back_populates="platform")
    events: Mapped[list["Event"]] = relationship(back_populates="platform")
    participants: Mapped[list["Participant"]] = relationship(back_populates="platform")


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("platform_id", "external_key", name="uq_locations_platform_external_key"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    country: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Площадка объявлена, но ещё не стартовала. Отдельно от is_paused: там
    # старты кончились, здесь их ещё не было (см. миграцию 064).
    is_upcoming: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_official_map: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    map_url: Mapped[str | None] = mapped_column(String(1024))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[SyncStatus | None] = mapped_column(Enum(SyncStatus, name="sync_status_enum"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship(back_populates="locations")
    events: Mapped[list["Event"]] = relationship(back_populates="location")
    event_summaries: Mapped[list["EventSummary"]] = relationship(back_populates="location")
    coordinate_requests: Mapped[list["LocationCoordinateRequest"]] = relationship(back_populates="location")
    catalog_links: Mapped[list["LocationCatalogLink"]] = relationship(back_populates="location")
    contacts: Mapped[list["LocationContact"]] = relationship(back_populates="location")
    description: Mapped["LocationDescription | None"] = relationship(
        back_populates="location", uselist=False, cascade="all, delete-orphan"
    )
    opening: Mapped["LocationOpening | None"] = relationship(
        back_populates="location", uselist=False, cascade="all, delete-orphan"
    )
    announce_settings: Mapped["LocationAnnounceSettings | None"] = relationship(
        back_populates="location", uselist=False
    )
    merge_requests_as_match: Mapped[list["LocationMergeRequest"]] = relationship(
        back_populates="matched_location",
        foreign_keys="LocationMergeRequest.matched_location_id",
    )


class LocationMergeRequest(Base):
    __tablename__ = "location_merge_requests"
    __table_args__ = (
        UniqueConstraint(
            "platform_id",
            "candidate_slug",
            "matched_location_id",
            name="uq_location_merge_requests_candidate_match",
        ),
        Index("ix_location_merge_requests_platform_status", "platform_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    candidate_slug: Mapped[str] = mapped_column(String(256), nullable=False)
    candidate_name: Mapped[str] = mapped_column(String(256), nullable=False)
    candidate_source_url: Mapped[str | None] = mapped_column(String(1024))
    matched_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    matched_slug: Mapped[str] = mapped_column(String(256), nullable=False)
    overlap_count: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap_event_dates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    status: Mapped[LocationMergeRequestStatus] = mapped_column(
        Enum(LocationMergeRequestStatus, name="location_merge_request_status_enum"),
        nullable=False,
        server_default="pending",
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship()
    matched_location: Mapped["Location"] = relationship(
        back_populates="merge_requests_as_match",
        foreign_keys=[matched_location_id],
    )


class LocationContact(Base):
    """Ссылка на чат локации (Telegram) — заполняется вручную из админки.

    У одной локации может быть несколько ссылок (основной чат, резервный,
    чат организаторов и т.п.) — поэтому location_id не уникален, а различать
    ссылки помогает необязательный label.
    """

    __tablename__ = "location_contacts"
    __table_args__ = (Index("ix_location_contacts_location_id", "location_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    telegram_url: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location: Mapped["Location"] = relationship(back_populates="contacts")


class LocationDescription(Base):
    """Описание площадки с сайта системы: когда старт, что за трасса, как доехать.

    Одна строка на локацию платформы (у идентичности их может быть несколько —
    5 вёрст и S95 пишут о своей площадке по-своему). Тексты чужие, поэтому
    source_url обязателен: на странице локации мы ставим ссылку на источник.

    Три отметки времени отвечают на разные вопросы, и путать их нельзя:
    `fetched_at` — когда мы последний раз СМОТРЕЛИ страницу (ставится всегда,
    даже если текст тот же и даже если страница оказалась пустой);
    `content_updated_at` — когда текст последний раз РЕАЛЬНО менялся;
    `revision` — сколько раз он менялся с момента первого сбора (0 — с тех пор
    не менялся ни разу). Так по строке видно «проверяли час назад, а менялось
    в марте», а не только «что-то происходило».

    content_hash — хеш собранного текста, а не HTML страницы: вёрстка на
    5verst.ru меняется от релиза к релизу, а описание парка — раз в год.
    Хеш по тексту даёт content_updated_at, которому можно верить.
    """

    __tablename__ = "location_descriptions"
    __table_args__ = (UniqueConstraint("location_id", name="uq_location_descriptions_location_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    # «Где и когда?»: адрес старта и время (бывает сезонным).
    schedule_text: Mapped[str | None] = mapped_column(Text)
    # «О трассе»: маршрут, покрытие, круги, место сбора.
    course_text: Mapped[str | None] = mapped_column(Text)
    # Вводная строка «как добраться»: адрес (5 вёрст) или место проведения (S95).
    travel_text: Mapped[str | None] = mapped_column(Text)
    # [{"title": "Общественным транспортом", "text": "…"}, …]
    travel_sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    # [{"title": "Карта и схема проезда", "url": "https://…"}, …]
    links: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    source_url: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Когда последний раз смотрели страницу — независимо от того, менялся текст или нет.
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Когда текст последний раз менялся, и сколько раз он менялся всего.
    content_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location: Mapped["Location"] = relationship(back_populates="description")


class LocationAnnounceSettings(Base):
    """Правила анонсов локации — одна строка на локацию (в легаси — contacts_location.not_report)."""

    __tablename__ = "location_announce_settings"
    __table_args__ = (
        UniqueConstraint("location_id", name="uq_location_announce_settings_location_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    do_not_disturb: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location: Mapped["Location"] = relationship(back_populates="announce_settings")


class LocationOpening(Base):
    """Какой старт считать торжественным открытием — ручная разметка.

    У 5 вёрст, parkrun и RunPark открытие видно из протокола (событие №1), у С95
    по номерам забегов его не опознать — там номер проставляется руками.

    Строка на локацию платформы, а не на физическую точку: одна и та же локация
    открывалась в parkrun и в 5 вёрст по своим номерам, и размечать их надо
    отдельно. В зачёт рейтинга при этом идёт только самое раннее из них: парк
    открывается один раз (см. _opening_event_ids в leaderboard_service).

    `opening_event_number IS NULL` при существующей строке означает не «не
    знаем», а «открытия у этой локации нет»: так гасится ложное открытие там,
    где система начала вести протоколы позже самой локации.
    """

    __tablename__ = "location_openings"
    __table_args__ = (UniqueConstraint("location_id", name="uq_location_openings_location_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    opening_event_number: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location: Mapped["Location"] = relationship(back_populates="opening")


class LocationCatalog(Base):
    """Canonical location identity for cross-platform display (parkrun → 5verst/s95)."""

    __tablename__ = "location_catalog"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False)
    legacy_parkrun_slug: Mapped[str | None] = mapped_column(String(256))
    active_platform: Mapped[str | None] = mapped_column(String(32))
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    links: Mapped[list["LocationCatalogLink"]] = relationship(
        back_populates="catalog",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("legacy_parkrun_slug", name="uq_location_catalog_legacy_parkrun_slug"),
        Index("ix_location_catalog_canonical_name", "canonical_name"),
    )


class LocationCatalogLink(Base):
    """Maps a platform location slug to a catalog entry without changing source location names."""

    __tablename__ = "location_catalog_links"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    catalog_id: Mapped[UUID] = mapped_column(ForeignKey("location_catalog.id", ondelete="CASCADE"), nullable=False)
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(256), nullable=False)
    location_id: Mapped[UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    catalog: Mapped["LocationCatalog"] = relationship(back_populates="links")
    platform: Mapped["Platform"] = relationship()
    location: Mapped["Location | None"] = relationship(back_populates="catalog_links")

    __table_args__ = (
        UniqueConstraint("platform_id", "external_key", name="uq_location_catalog_links_platform_external_key"),
        Index("ix_location_catalog_links_catalog_id", "catalog_id"),
    )


class Club(Base):
    """Running club parsed from a platform clubs listing (5verst /clubs/, later s95.ru/clubs)."""

    __tablename__ = "clubs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(String(1024))
    # Stats from the clubs list table (5verst columns + S95 columns for the future).
    members_count: Mapped[int | None] = mapped_column(Integer)
    finishes_count: Mapped[int | None] = mapped_column(Integer)
    volunteerings_count: Mapped[int | None] = mapped_column(Integer)
    avg_time_seconds: Mapped[int | None] = mapped_column(Integer)
    best_female_time_seconds: Mapped[int | None] = mapped_column(Integer)
    best_male_time_seconds: Mapped[int | None] = mapped_column(Integer)
    best_time_seconds: Mapped[int | None] = mapped_column(Integer)
    avg_finishes_per_member: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    avg_volunteerings_per_member: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    external_links: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    stats_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # List-sync bookkeeping: row hash detects column changes, detail_synced_at drives rotation.
    list_row_hash: Mapped[str | None] = mapped_column(String(64))
    list_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    needs_detail_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    detail_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail_source_hash: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship()
    members: Mapped[list["ClubMember"]] = relationship(back_populates="club", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("platform_id", "external_key", name="uq_clubs_platform_external_key"),
        Index("ix_clubs_rotation", "platform_id", "is_active", "needs_detail_sync", "detail_synced_at"),
    )


class ClubMember(Base):
    """Club roster membership (composition only, no per-member stats)."""

    __tablename__ = "club_members"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False)
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    club: Mapped["Club"] = relationship(back_populates="members")
    participant: Mapped["Participant"] = relationship()

    __table_args__ = (
        UniqueConstraint("club_id", "participant_id", name="uq_club_members_club_participant"),
        Index("ix_club_members_participant_id", "participant_id"),
    )


class ClubCatalog(Base):
    """Canonical club identity for cross-platform merging (5verst ↔ s95)."""

    __tablename__ = "club_catalog"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    links: Mapped[list["ClubCatalogLink"]] = relationship(
        back_populates="catalog",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_club_catalog_normalized_name", "normalized_name"),)


class ClubCatalogLink(Base):
    """Maps a platform club slug to a catalog entry; manual links are never overridden by auto-match."""

    __tablename__ = "club_catalog_links"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    catalog_id: Mapped[UUID] = mapped_column(ForeignKey("club_catalog.id", ondelete="CASCADE"), nullable=False)
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(256), nullable=False)
    club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id", ondelete="SET NULL"))
    link_source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="auto_name")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    catalog: Mapped["ClubCatalog"] = relationship(back_populates="links")
    platform: Mapped["Platform"] = relationship()
    club: Mapped["Club | None"] = relationship()

    __table_args__ = (
        UniqueConstraint("platform_id", "external_key", name="uq_club_catalog_links_platform_external_key"),
        Index("ix_club_catalog_links_catalog_id", "catalog_id"),
    )


class RunparkLocationMapping(Base):
    """Correspondence between runpark export location_id and our platform locations."""

    __tablename__ = "runpark_location_mappings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    runpark_location_id: Mapped[str] = mapped_column(String(64), nullable=False)
    runpark_slug: Mapped[str | None] = mapped_column(String(256))
    runpark_name: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    show_on_map: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_transitional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(128))
    matched_platform: Mapped[str | None] = mapped_column(String(32))
    matched_external_key: Mapped[str | None] = mapped_column(String(256))
    matched_location_id: Mapped[UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    legacy_parkrun_slug: Mapped[str | None] = mapped_column(String(256))
    runpark_location_row_id: Mapped[UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    public_url: Mapped[str | None] = mapped_column(String(1024))
    duplicate_match_text: Mapped[str | None] = mapped_column(Text)
    decision_raw: Mapped[str | None] = mapped_column(Text)
    source_batch: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    matched_location: Mapped["Location | None"] = relationship(foreign_keys=[matched_location_id])
    runpark_location_row: Mapped["Location | None"] = relationship(foreign_keys=[runpark_location_row_id])

    __table_args__ = (
        UniqueConstraint("runpark_location_id", name="uq_runpark_location_mappings_runpark_location_id"),
        Index("ix_runpark_location_mappings_decision", "decision"),
        Index("ix_runpark_location_mappings_show_on_map", "show_on_map"),
    )


class LocationCoordinateRequest(Base):
    __tablename__ = "location_coordinate_requests"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    map_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="awaiting_coordinates")
    proposed_latitude: Mapped[float | None] = mapped_column()
    proposed_longitude: Mapped[float | None] = mapped_column()
    admin_telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    verify_telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    location: Mapped["Location"] = relationship(back_populates="coordinate_requests")


class EventSummary(Base):
    __tablename__ = "event_summaries"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_event_key", name="uq_event_summaries_platform_external_key"),
        Index("ix_event_summaries_location_event_date", "location_id", "event_date"),
        Index("ix_event_summaries_summary_hash", "summary_hash"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))
    external_event_key: Mapped[str] = mapped_column(String(512), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_number: Mapped[int | None] = mapped_column(Integer)
    is_test_event: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    finishers_count: Mapped[int | None] = mapped_column(Integer)
    volunteers_count: Mapped[int | None] = mapped_column(Integer)
    avg_time_sec: Mapped[int | None] = mapped_column(Integer)
    avg_time_display: Mapped[str | None] = mapped_column(String(32))
    best_female_time_sec: Mapped[int | None] = mapped_column(Integer)
    best_female_time_display: Mapped[str | None] = mapped_column(String(32))
    best_male_time_sec: Mapped[int | None] = mapped_column(Integer)
    best_male_time_display: Mapped[str | None] = mapped_column(String(32))
    summary_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[SyncStatus | None] = mapped_column(Enum(SyncStatus, name="sync_status_enum", create_constraint=False))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship()
    location: Mapped["Location"] = relationship(back_populates="event_summaries")
    event: Mapped["Event | None"] = relationship(back_populates="summary")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_event_key", name="uq_events_platform_external_key"),
        Index("ix_events_platform_event_date", "platform_id", "event_date"),
        Index("ix_events_location_event_date", "location_id", "event_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    external_event_key: Mapped[str] = mapped_column(String(512), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_number: Mapped[int | None] = mapped_column(Integer)
    is_test_event: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    title: Mapped[str | None] = mapped_column(String(512))
    runners_count: Mapped[int | None] = mapped_column(Integer)
    finishers_count: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[SyncStatus | None] = mapped_column(Enum(SyncStatus, name="sync_status_enum", create_constraint=False))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship(back_populates="events")
    location: Mapped["Location"] = relationship(back_populates="events")
    summary: Mapped["EventSummary | None"] = relationship(back_populates="event", uselist=False)
    run_results: Mapped[list["RunResult"]] = relationship(back_populates="event")
    volunteer_results: Mapped[list["VolunteerResult"]] = relationship(back_populates="event")
    protocol_sync_state: Mapped["ProtocolSyncState | None"] = relationship(
        back_populates="event", uselist=False
    )


class EventCrosslink(Base):
    __tablename__ = "event_crosslinks"
    __table_args__ = (
        UniqueConstraint("primary_event_id", "secondary_event_id"),
        Index("ix_event_crosslinks_primary", "primary_event_id"),
        Index("ix_event_crosslinks_secondary", "secondary_event_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    primary_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    secondary_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProtocolSyncState(Base):
    __tablename__ = "protocol_sync_states"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_protocol_sync_states_event_id"),
        Index("ix_protocol_sync_states_last_check", "last_protocol_check_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    event_summary_id: Mapped[UUID | None] = mapped_column(ForeignKey("event_summaries.id", ondelete="SET NULL"))
    last_protocol_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_protocol_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protocol_source_hash: Mapped[str | None] = mapped_column(String(64))
    # summary_hash саммари на момент последней успешной закачки протокола.
    # Отличается от EventSummary.summary_hash → протокол отстал от витрины
    # и его надо перечитать (app/sync/protocol_debt.py).
    summary_hash_at_fetch: Mapped[str | None] = mapped_column(String(64))
    finishers_at_fetch: Mapped[int | None] = mapped_column(Integer)
    run_results_count: Mapped[int | None] = mapped_column(Integer)
    volunteer_results_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    event: Mapped["Event"] = relationship(back_populates="protocol_sync_state")
    event_summary: Mapped["EventSummary | None"] = relationship()


class ProfileFetchPending(Base):
    __tablename__ = "profile_fetch_pending"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_input: Mapped[str] = mapped_column(Text, nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(128))
    canonical_profile_url: Mapped[str | None] = mapped_column(String(1024))
    operation: Mapped[ProfileFetchPendingOperation] = mapped_column(
        Enum(ProfileFetchPendingOperation, name="profile_fetch_pending_operation_enum", create_constraint=False),
        nullable=False,
        server_default=ProfileFetchPendingOperation.profile_preview.value,
    )
    status: Mapped[ProfileFetchPendingStatus] = mapped_column(
        Enum(ProfileFetchPendingStatus, name="profile_fetch_pending_status_enum", create_constraint=False),
        nullable=False,
        server_default=ProfileFetchPendingStatus.pending.value,
    )
    reason: Mapped[ProfileFetchPendingReason] = mapped_column(
        Enum(ProfileFetchPendingReason, name="profile_fetch_pending_reason_enum", create_constraint=False),
        nullable=False,
        server_default=ProfileFetchPendingReason.cooldown.value,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_user_id", name="uq_participants_platform_external_user_id"),
        Index("ix_participants_platform_external_user_id", "platform_id", "external_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    profile_url: Mapped[str | None] = mapped_column(String(1024))
    age_category: Mapped[str | None] = mapped_column(String(64))
    # «male» / «female» / NULL. Материализация того, что раньше считалось из
    # age_category (или profile_extra у s95) при каждом чтении — см.
    # gender_position_service.resolve_participant_gender.
    gender: Mapped[str | None] = mapped_column(String(8))
    club_name: Mapped[str | None] = mapped_column(String(256))
    barcode_id: Mapped[str | None] = mapped_column(String(16))
    planning_location: Mapped[str | None] = mapped_column(String(256))
    planning_location_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Когда страницу профиля реально открывали и парсили (в отличие от fetched_at,
    # который обновляется при любом касании строки, в т.ч. из импорта результатов).
    profile_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[SyncStatus | None] = mapped_column(Enum(SyncStatus, name="sync_status_enum", create_constraint=False))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    platform: Mapped["Platform"] = relationship(back_populates="participants")
    run_results: Mapped[list["RunResult"]] = relationship(back_populates="participant")
    volunteer_results: Mapped[list["VolunteerResult"]] = relationship(back_populates="participant")
    platform_links: Mapped[list["PlatformLink"]] = relationship(back_populates="participant")


class RunResult(Base):
    __tablename__ = "run_results"
    __table_args__ = (
        UniqueConstraint("event_id", "external_result_key", name="uq_run_results_event_external_key"),
        Index("ix_run_results_participant_event", "participant_id", "event_id"),
        Index("ix_run_results_event_position", "event_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    participant_id: Mapped[UUID | None] = mapped_column(ForeignKey("participants.id"))
    external_result_key: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    gender_position: Mapped[int | None] = mapped_column(Integer)
    finish_time_sec: Mapped[int | None] = mapped_column(Integer)
    finish_time_display: Mapped[str | None] = mapped_column(String(32))
    pace_sec_per_km: Mapped[int | None] = mapped_column(Integer)
    pace_display: Mapped[str | None] = mapped_column(String(16))
    age_category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(64))
    is_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_first_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_first_run_at_location: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_global_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_location_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    club_name: Mapped[str | None] = mapped_column(String(256))
    achievement_labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    event: Mapped["Event"] = relationship(back_populates="run_results")
    participant: Mapped["Participant | None"] = relationship(back_populates="run_results")


class VolunteerResult(Base):
    __tablename__ = "volunteer_results"
    __table_args__ = (
        UniqueConstraint("event_id", "external_result_key", name="uq_volunteer_results_event_external_key"),
        Index("ix_volunteer_results_participant_event", "participant_id", "event_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    participant_id: Mapped[UUID | None] = mapped_column(ForeignKey("participants.id"))
    external_result_key: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    event: Mapped["Event"] = relationship(back_populates="volunteer_results")
    participant: Mapped["Participant | None"] = relationship(back_populates="volunteer_results")


def _media_url(key: str | None) -> str | None:
    """Ссылка на картинку в хранилище.

    Ключ с "/" — новый формат (media-хранилище: публичный S3 на проде,
    /api/media локально). Значение без "/" — аватарка, загруженная до переезда
    на S3: это имя файла на диске, которое ещё отдаёт роут /api/avatars.
    """
    if not key:
        return None
    if "/" not in key:
        return f"/api/avatars/{key}"
    from app.core.media_storage import get_media_storage

    return get_media_storage().public_url(key)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=False, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(128))
    telegram_first_name: Mapped[str | None] = mapped_column(String(128))
    telegram_last_name: Mapped[str | None] = mapped_column(String(128))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    # Материализованный результат: имя считается из профилей беговых систем
    # (см. app.services.user_display_name_service). Все 20+ мест, которые имя
    # читают — рейтинги, страницы локаций, публичный профиль, OG-карточки —
    # продолжают читать это поле, а не пересчитывать самостоятельно.
    display_name: Mapped[str | None] = mapped_column(String(128))
    # УСТАРЕЛО с 25.08.2026: свободного ввода имени больше нет, поле не читается
    # и не пишется. Оставлено как архив прежних ручных значений на случай отката.
    display_name_customized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Как показывать имя: "auto" — полное («Иван Петров»), "initial" — «Иван П.».
    # Канон значений — DISPLAY_NAME_STYLES в user_display_name_service.
    display_name_style: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto", server_default="auto"
    )
    # Зафиксированная система-источник имени. Пересматривается ТОЛЬКО при
    # привязке и отвязке профиля — фоновый пересчёт её не трогает, иначе имя
    # человека менялось бы само по себе. NULL — привязок нет, имя от провайдера.
    display_name_platform_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="SET NULL")
    )
    # Источник выбран человеком в настройках, а не алгоритмом. Такой выбор не
    # перебивается новой привязкой: молча меняем имя только тем, кто его не
    # выбирал сам.
    display_name_source_manual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Прежнее имя для одноразовой плашки в кабинете («было pele1985»). Ставится
    # только бэкфиллом при переходе на имена из профилей; NULL — плашка не нужна
    # либо человек её закрыл.
    display_name_notice: Mapped[str | None] = mapped_column(String(128))
    # Предложение сменить источник, которое человек отклонил («оставить как
    # есть»). Пока алгоритм предлагает то же самое имя, баннер не показывается.
    display_name_dismissed_name: Mapped[str | None] = mapped_column(String(128))
    consent_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    consent_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    news_subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    profile_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    home_location_key: Mapped[str | None] = mapped_column(String(255))
    # Когда человек в последний раз менял домашнюю локацию руками (в т.ч.
    # сбрасывал на авто). NULL — не менял никогда. Нужно рейтингу дальности: его
    # таблица кэшируется на несколько часов, и без этой отметки нельзя отличить
    # «в таблице ещё старые километры» от «рейтинг посчитан неправильно».
    home_location_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Уникальная НЕцифровая ссылка на публичный профиль (/users/{public_slug});
    # хранится в нижнем регистре, уникальность регистронезависима. NULL — не задана.
    public_slug: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Имя файла аватарки в settings.avatars_dir ("{user_id}-{token}.jpg").
    # NULL — аватарки нет. Сам файл живёт на диске, при замене старый удаляется.
    avatar_path: Mapped[str | None] = mapped_column(String(512))
    # Оригинал без пережатия — открывается по клику на аватарку (решение
    # Дмитрия 28.07.2026: «не будем их сжимать, а по клику раскрывать»).
    # NULL — аватарка загружена до появления оригиналов либо её нет.
    avatar_full_path: Mapped[str | None] = mapped_column(String(512))
    serial_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        server_default=text("nextval('users_serial_id_seq')"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_auto_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_sync_by_platform: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default='{"five_verst": false, "s95": false, "parkrun": false}',
    )
    # Виды вех «Моя история», которые пользователь скрыл у себя (список kind'ов).
    # Отсутствие kind в списке = включён (по умолчанию). Персональный аналог
    # админского history_milestone_settings; канон kind'ов —
    # app.history_milestone_kinds.MILESTONE_KINDS.
    history_disabled_kinds: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="[]",
    )

    platform_links: Mapped[list["PlatformLink"]] = relationship(back_populates="user")
    dashboard_cache: Mapped["DashboardCache | None"] = relationship(back_populates="user", uselist=False)
    sync_jobs: Mapped[list["SyncJob"]] = relationship(back_populates="user")
    auth_identities: Mapped[list["AuthIdentity"]] = relationship(back_populates="user")

    @property
    def avatar_url(self) -> str | None:
        """Публичный адрес аватарки; UserResponse (from_attributes) подхватывает
        это свойство, поэтому avatar_url есть везде, где отдаётся пользователь."""
        return _media_url(self.avatar_path)

    @property
    def avatar_full_url(self) -> str | None:
        """Оригинал аватарки — им открывается просмотр по клику. Если оригинала
        нет (аватарка старая), фронт показывает превью."""
        return _media_url(self.avatar_full_path)


class BlockedProfileSlug(Base):
    """Slug'и публичного профиля, зарезервированные вручную (нельзя занять).

    В отличие от статического RESERVED_SLUGS (служебные пути приложения), это
    редактируемый через админку список: чей-то ник, который мы держим свободным
    по просьбе/на будущее. Хранится в нижнем регистре — сравнение с public_slug
    регистронезависимо.
    """

    __tablename__ = "blocked_profile_slugs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_auth_identities_provider_external_id"),
        Index("ix_auth_identities_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider_enum"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(256))
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="auth_identities")


class PlatformLink(Base):
    __tablename__ = "platform_links"
    __table_args__ = (
        UniqueConstraint("platform_id", "external_user_id", name="uq_platform_links_platform_external_user_id"),
        UniqueConstraint("user_id", "platform_id", name="uq_platform_links_user_platform"),
        Index("ix_platform_links_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    participant_id: Mapped[UUID | None] = mapped_column(ForeignKey("participants.id"))
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_user_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[PlatformLinkSyncStatus] = mapped_column(
        Enum(PlatformLinkSyncStatus, name="platform_link_sync_status_enum"),
        nullable=False,
        server_default="idle",
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="platform_links")
    platform: Mapped["Platform"] = relationship()
    participant: Mapped["Participant | None"] = relationship(back_populates="platform_links")
    sync_jobs: Mapped[list["SyncJob"]] = relationship(back_populates="platform_link")


class DashboardCache(Base):
    __tablename__ = "dashboard_cache"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    user: Mapped["User"] = relationship(back_populates="dashboard_cache")


class AuthLoginRequest(Base):
    __tablename__ = "auth_login_requests"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    request_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[AuthLoginRequestStatus] = mapped_column(
        Enum(AuthLoginRequestStatus, name="auth_login_request_status_enum"),
        nullable=False,
        server_default="pending",
    )
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthOneTimeToken(Base):
    __tablename__ = "auth_one_time_tokens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    login_request_id: Mapped[UUID] = mapped_column(ForeignKey("auth_login_requests.id"), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SyncWatermark(Base):
    """Key/value sync progress markers, e.g. the timestamp through which all S95
    protocols have been reconciled (for future ?since= incremental sync)."""

    __tablename__ = "sync_watermarks"
    __table_args__ = (
        UniqueConstraint("platform_id", "key", name="uq_sync_watermarks_platform_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_platform_started_at", "platform_id", "started_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    platform_id: Mapped[UUID] = mapped_column(ForeignKey("platforms.id"), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[SyncRunStatus] = mapped_column(
        Enum(SyncRunStatus, name="sync_run_status_enum"), nullable=False, server_default="queued"
    )
    parser_version: Mapped[str | None] = mapped_column(String(32))
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_upserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    platform: Mapped["Platform"] = relationship()
    log_entries: Mapped[list["SyncLogEntry"]] = relationship(back_populates="sync_run")


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (Index("ix_sync_jobs_user_created_at", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform_link_id: Mapped[UUID | None] = mapped_column(ForeignKey("platform_links.id"))
    trigger: Mapped[SyncJobTrigger] = mapped_column(Enum(SyncJobTrigger, name="sync_job_trigger_enum"), nullable=False)
    status: Mapped[SyncJobStatus] = mapped_column(
        Enum(SyncJobStatus, name="sync_job_status_enum"), nullable=False, server_default="queued"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="sync_jobs")
    platform_link: Mapped["PlatformLink | None"] = relationship(back_populates="sync_jobs")
    log_entries: Mapped[list["SyncLogEntry"]] = relationship(back_populates="sync_job")


class SyncLogEntry(Base):
    __tablename__ = "sync_log_entries"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    sync_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("sync_runs.id"))
    sync_job_id: Mapped[UUID | None] = mapped_column(ForeignKey("sync_jobs.id"))
    level: Mapped[SyncLogLevel] = mapped_column(Enum(SyncLogLevel, name="sync_log_level_enum"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sync_run: Mapped["SyncRun | None"] = relationship(back_populates="log_entries")
    sync_job: Mapped["SyncJob | None"] = relationship(back_populates="log_entries")


class ScheduledRunLog(Base):
    """История запусков задач автообновления (celery beat).

    Раньше каждый запуск уходил админу в ВК двумя сообщениями («запуск» /
    «завершено»). Теперь запуск пишется сюда, админка «Автообновление» листает
    историю, а в ВК уходит одна сводка в сутки (admin_digest.daily_sync_summary).
    Детализация по локациям (DETAIL_LIST_KEYS) в payload не сохраняется — в
    истории нужны итоги, а не перечисление площадок.
    """

    __tablename__ = "scheduled_run_logs"
    __table_args__ = (
        Index("ix_scheduled_run_logs_started_at", "started_at"),
        Index("ix_scheduled_run_logs_platform_started_at", "platform", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, server_default="other")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    errors: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LocationRating(Base):
    __tablename__ = "location_ratings"
    __table_args__ = (
        # Уникальность по типу участия: два частичных индекса (см. миграцию 039).
        Index(
            "uq_location_ratings_user_run",
            "user_id",
            "run_result_id",
            unique=True,
            postgresql_where=text("run_result_id IS NOT NULL"),
        ),
        Index(
            "uq_location_ratings_user_volunteer",
            "user_id",
            "volunteer_result_id",
            unique=True,
            postgresql_where=text("volunteer_result_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(run_result_id IS NOT NULL) <> (volunteer_result_id IS NOT NULL)",
            name="ck_location_ratings_one_source",
        ),
        CheckConstraint("score_overall BETWEEN 1 AND 5", name="ck_location_ratings_overall"),
        CheckConstraint(
            "score_organization IS NULL OR score_organization BETWEEN 1 AND 5",
            name="ck_location_ratings_organization",
        ),
        CheckConstraint(
            "score_route IS NULL OR score_route BETWEEN 1 AND 5",
            name="ck_location_ratings_route",
        ),
        CheckConstraint(
            "score_community IS NULL OR score_community BETWEEN 1 AND 5",
            name="ck_location_ratings_community",
        ),
        Index("ix_location_ratings_location_key", "location_key"),
        Index("ix_location_ratings_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Оценка привязана либо к пробежке (бегун), либо к волонтёрству — ровно одно
    # из двух заполнено (CHECK ck_location_ratings_one_source).
    run_result_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("run_results.id", ondelete="CASCADE"), nullable=True
    )
    volunteer_result_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("volunteer_results.id", ondelete="CASCADE"), nullable=True
    )
    # 'run' | 'volunteer' — как участник был на старте.
    participation_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="run"
    )
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"), nullable=False)
    # canonical identity key локации (как home_location_key) — для агрегации рейтинга
    location_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    score_overall: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    score_organization: Mapped[int | None] = mapped_column(SmallInteger)
    score_route: Mapped[int | None] = mapped_column(SmallInteger)
    score_community: Mapped[int | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    # Показывать ли автора наружу; в БД оценка всегда не анонимна.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserGoal(Base):
    """Цель пользователя на календарный год.

    goal_type — код пресета из achievements_service.GOAL_PRESETS (свободного
    текста нет). target_value — планка в единицах пресета: штуки для объёмных
    целей, секунды для целей на время. Прогресс считается на лету.
    """

    __tablename__ = "user_goals"
    __table_args__ = (
        UniqueConstraint("user_id", "year", "goal_type", name="uq_user_goals_user_year_type"),
        Index("ix_user_goals_user_year", "user_id", "year"),
        CheckConstraint("target_value > 0", name="ck_user_goals_target_positive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    goal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PageViewEvent(Base):
    """Сырое событие просмотра страницы (собственная аналитика сайта).

    Пишется эндпоинтом POST /stats/pageview на каждую смену роута на фронте;
    duration_sec дозаполняется беконом POST /stats/pageleave (view_id —
    сгенерированный клиентом идентификатор просмотра). Строки живут
    ограниченный срок (settings.page_events_retention_days) — вечная история
    хранится в агрегатах page_stats_daily.
    """

    __tablename__ = "page_view_events"
    __table_args__ = (
        Index("ix_page_view_events_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    view_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Ключ сущности внутри раздела: user_id владельца для профилей, slug для
    # локаций, metric для рейтингов. Пустая строка — раздел без сущности.
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    # "u:<user_id>" либо "a:<анонимный id браузера>" — как в Redis-счётчике.
    visitor_key: Mapped[str | None] = mapped_column(String(80))
    viewer_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Владелец смотрит свой собственный профиль (только для page_type=profile).
    is_self: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    duration_sec: Mapped[int | None] = mapped_column(Integer)


class UserGeoPing(Base):
    """Огрублённая отметка «где был участник, когда открывал карту».

    Пишется, только если человек сам разрешил браузеру определять положение
    (карта показывает ему точку «вы здесь»), и не чаще одной строки в сутки —
    это держит уникальная пара user_id + observed_on.

    Точные координаты сюда не попадают: широта и долгота приходят с фронта уже
    округлёнными до двух знаков и округляются ещё раз на сервере — клетка
    примерно километр на километр. На таком масштабе видно город и район, но не
    дом и не работу, а нужны отметки ровно для двух вопросов: где есть участники
    без площадки поблизости и верно ли сайт угадывает домашнюю локацию.
    """

    __tablename__ = "user_geo_pings"
    __table_args__ = (
        UniqueConstraint("user_id", "observed_on", name="uq_user_geo_pings_user_day"),
        Index("ix_user_geo_pings_observed_on", "observed_on"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    # Погрешность определения в метрах: у вышек и Wi-Fi она в километрах, и при
    # разборе такие отметки стоит отличать от честного GPS.
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    nearest_identity_key: Mapped[str | None] = mapped_column(String(128))
    nearest_distance_km: Mapped[float | None] = mapped_column()
    # Домашняя локация на момент отметки — она может смениться, поэтому храним,
    # а не вычисляем задним числом.
    home_identity_key: Mapped[str | None] = mapped_column(String(128))
    home_distance_km: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LoginEvent(Base):
    """Журнал входов и выходов: по строке на каждый логин и на каждый разлогин.

    Нужен, чтобы отличать «сессия слетела сама» от «зашёл с другого устройства»
    и «вышел сам». Диагностика читается так: логин без предшествующего logout
    с тем же device_ref — сессия оборвалась не по воле пользователя.

    session_ref — префикс sha256 от session_id (сам id не храним, он равносилен
    паролю); связывает login и logout одной сессии. device_ref — хэш от
    user_agent + ip, грубый отпечаток устройства.
    """

    __tablename__ = "login_events"
    __table_args__ = (
        Index("ix_login_events_user_ts", "user_id", "ts"),
        Index("ix_login_events_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # login | logout
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # yandex | vk | telegram | magic_link | merge | "" (для logout)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    session_ref: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    user_agent: Mapped[str] = mapped_column(String(256), nullable=False, server_default="")
    device_ref: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")


class AbEvent(Base):
    """Сырое событие АБ-эксперимента (скролл, клики, конверсия) с вариантом.

    Пишется эндпоинтом POST /stats/event. visitor_key здесь ВСЕГДА анонимный
    ("a:<id браузера>") — в отличие от page_view_events, где после логина ключ
    меняется на "u:<user_id>". Так события до и после VK-редиректа сшиваются
    в одну воронку; пользователь при этом виден в viewer_user_id.

    cohort заполняется только для event_type=login_complete: "new" — логин
    создал аккаунт (регистрация), "returning" — вошёл ранее зарегистрированный
    (разлогиненный) участник; в конверсию эксперимента идут только "new".
    """

    __tablename__ = "ab_events"
    __table_args__ = (
        Index("ix_ab_events_experiment_ts", "experiment", "ts"),
        Index("ix_ab_events_visitor", "visitor_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    experiment: Mapped[str] = mapped_column(String(32), nullable=False)
    variant: Mapped[str] = mapped_column(String(8), nullable=False)
    visitor_key: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    path: Mapped[str] = mapped_column(String(256), nullable=False, server_default="")
    cohort: Mapped[str] = mapped_column(String(16), nullable=False, server_default="")
    viewer_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PageStatsDaily(Base):
    """Дневной агрегат просмотров по (день МСК, раздел, сущность).

    Пересчитывается celery-задачей page_stats.rollup (upsert последних дней),
    хранится вечно — источник для страницы «Популярность» и годовых рубрик.
    avg duration = total_duration_sec / duration_views (события без
    зафиксированной длительности в среднее не входят).
    """

    __tablename__ = "page_stats_daily"
    __table_args__ = (
        UniqueConstraint("date", "page_type", "entity_key", name="uq_page_stats_daily_date_type_entity"),
        Index("ix_page_stats_daily_type_entity", "page_type", "entity_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unique_viewers: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    self_views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_duration_sec: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    duration_views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class BlogPost(Base):
    """Пост блога — затравка публикации из Telegram-канала @popov_way.

    Полный текст живёт в канале: сайт хранит заголовок, затравку и ссылку
    t.me, карточка маршрутизирует читателя в Telegram (пост + комментарии).
    clicks_count — счётчик переходов с сайта, основа сортировки «популярные».
    """

    __tablename__ = "blog_posts"
    __table_args__ = (Index("ix_blog_posts_published_at", "published_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    teaser: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_url: Mapped[str] = mapped_column(String(512), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    clicks_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SiteRelease(Base):
    """Релиз сайта — блок на публичной странице «Обновления» (/updates).

    Версия X.Y.Z (опционально с суффиксом -fixN) присваивается при деплое по
    протоколу из docs/release_management.md. Запись создаётся скрытой
    (is_published=false): администратор правит текст и сам открывает релиз
    на сайте. Скрытые и удалённые релизы оставляют пропуски в опубликованных
    номерах — это допустимо.
    """

    __tablename__ = "site_releases"
    __table_args__ = (Index("ix_site_releases_released_at", "released_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    version: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    released_at: Mapped[date] = mapped_column(Date, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class HistoryMilestoneSetting(Base):
    """Вкл/выкл конкретного вида вехи «Моя история» (админ-переключатель).

    Строка существует только для вех, которые администратор явно выключил —
    отсутствие строки для kind означает enabled=true (значение по умолчанию).
    Канонический список kind'ов — app.history_milestone_kinds.MILESTONE_KINDS.
    """

    __tablename__ = "history_milestone_settings"

    kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BacklogCard(Base):
    """Карточка бэклога — идея/баг, предложенные пользователем сайта.

    score = upvotes - downvotes, денормализован для сортировки ленты по
    голосам без пересчёта join'ом на каждый запрос. category — свободная
    строка, валидируется по app.backlog_categories.CATEGORIES (без DB-enum,
    чтобы список можно было расширять без миграции).
    """

    __tablename__ = "backlog_cards"
    __table_args__ = (Index("ix_backlog_cards_status_score", "status", "score"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    type: Mapped[BacklogCardType] = mapped_column(Enum(BacklogCardType, name="backlog_card_type_enum"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, server_default="other")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[BacklogCardStatus] = mapped_column(
        Enum(BacklogCardStatus, name="backlog_card_status_enum"), nullable=False, server_default="pending"
    )
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    author_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    upvotes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    downvotes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Когда карточку перевели в «реализовано» (последний перевод). NULL — ещё нет.
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    author: Mapped["User"] = relationship()


class BacklogVote(Base):
    __tablename__ = "backlog_votes"

    card_id: Mapped[UUID] = mapped_column(ForeignKey("backlog_cards.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship()


class BacklogComment(Base):
    __tablename__ = "backlog_comments"
    __table_args__ = (Index("ix_backlog_comments_card_created_at", "card_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    card_id: Mapped[UUID] = mapped_column(ForeignKey("backlog_cards.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    author: Mapped["User"] = relationship()


class LocationRatingPhoto(Base):
    """Фото, приложенное к отзыву на локацию (до 5 на отзыв).

    storage_key — ключ объекта в публичном бакете (или относительный путь в
    локальном media_dir, см. core/media_storage.py); сам URL не храним, чтобы
    смена домена/бакета не требовала переписывания строк в БД.
    """

    __tablename__ = "location_rating_photos"
    __table_args__ = (Index("ix_location_rating_photos_rating", "rating_id", "sort_order"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    rating_id: Mapped[UUID] = mapped_column(
        ForeignKey("location_ratings.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BacklogCardPhoto(Base):
    """Фото, приложенное к карточке бэклога (до 3 на карточку)."""

    __tablename__ = "backlog_card_photos"
    __table_args__ = (Index("ix_backlog_card_photos_card", "card_id", "sort_order"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    card_id: Mapped[UUID] = mapped_column(ForeignKey("backlog_cards.id", ondelete="CASCADE"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
