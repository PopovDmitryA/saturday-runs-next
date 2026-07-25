"""Бэклог: карточки идей/багов от пользователей, голосование, комментарии.

Публичная выдача (BacklogCardResponse/BacklogCommentResponse) скрывает автора
анонимных карточек/комментариев для всех; админская выдача
(BacklogCardAdminResponse) всегда показывает реального автора — модерация
должна знать, кто пишет, даже если это скрыто от других пользователей сайта.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session, joinedload

from app.backlog_categories import BACKLOG_CATEGORY_KEYS
from app.config import get_settings
from app.core.admin import is_admin_user
from app.models import BacklogCard, BacklogCardStatus, BacklogCardType, BacklogComment, BacklogVote, User
from app.schemas.backlog import (
    BacklogCardAdminResponse,
    BacklogCardResponse,
    BacklogCommentResponse,
    BacklogVoteAdminItem,
    BacklogVoteAdminListResponse,
)
from app.services.vk_admin_notify import send_vk_admin_message

_TYPE_LABELS = {BacklogCardType.bug: "баг", BacklogCardType.feature: "фича"}
_COMMENT_PREVIEW_LIMIT = 200
_DESCRIPTION_PREVIEW_LIMIT = 150


class BacklogError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _author_display(user: User) -> tuple[str, str]:
    name = user.display_name or f"Участник #{user.serial_id}"
    handle = user.public_slug or str(user.serial_id)
    return name, handle


def _card_link(card_id: UUID) -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/backlog?card={card_id}"


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _notify_new_card(card: BacklogCard) -> None:
    author = "аноним" if card.is_anonymous else _author_display(card.author)[0]
    text = (
        f"🆕 Новая карточка бэклога: [{_TYPE_LABELS[card.type]}] «{card.title}» от {author}\n"
        f"{_truncate(card.description, _DESCRIPTION_PREVIEW_LIMIT)}\n\n{_card_link(card.id)}"
    )
    send_vk_admin_message(text)


def _notify_new_vote(card: BacklogCard, value: int) -> None:
    mark = "👍" if value > 0 else "👎"
    text = (
        f"{mark} Доп. голос за «{card.title}»: счёт теперь {card.score} "
        f"({card.upvotes}/{card.downvotes})\n\n{_card_link(card.id)}"
    )
    send_vk_admin_message(text)


def _notify_new_comment(card: BacklogCard, comment: BacklogComment) -> None:
    author = "аноним" if comment.is_anonymous else _author_display(comment.author)[0]
    text = (
        f"💬 Комментарий к «{card.title}» от {author}: "
        f"{_truncate(comment.body, _COMMENT_PREVIEW_LIMIT)}\n\n{_card_link(card.id)}"
    )
    send_vk_admin_message(text)


def _get_card(db: Session, card_id: UUID, *, for_update_author: bool = False) -> BacklogCard:
    query = select(BacklogCard).where(BacklogCard.id == card_id)
    if for_update_author:
        query = query.options(joinedload(BacklogCard.author))
    card = db.execute(query).unique().scalar_one_or_none()
    if card is None:
        raise BacklogError("Карточка не найдена", status_code=404)
    return card


def _comment_counts(db: Session, card_ids: list[UUID]) -> dict[UUID, int]:
    if not card_ids:
        return {}
    rows = db.execute(
        select(BacklogComment.card_id, func.count())
        .where(BacklogComment.card_id.in_(card_ids))
        .group_by(BacklogComment.card_id)
    ).all()
    return dict(rows)


def _my_votes(db: Session, card_ids: list[UUID], viewer_id: UUID | None) -> dict[UUID, int]:
    if not card_ids or viewer_id is None:
        return {}
    rows = db.execute(
        select(BacklogVote.card_id, BacklogVote.value).where(
            BacklogVote.card_id.in_(card_ids), BacklogVote.user_id == viewer_id
        )
    ).all()
    return dict(rows)


def _card_to_response(
    card: BacklogCard, *, my_vote: int, comment_count: int, viewer_id: UUID | None
) -> BacklogCardResponse:
    author_name, author_handle = (None, None) if card.is_anonymous else _author_display(card.author)
    return BacklogCardResponse(
        id=card.id,
        type=card.type,
        category=card.category,
        title=card.title,
        description=card.description,
        status=card.status,
        is_anonymous=card.is_anonymous,
        author_display_name=author_name,
        author_handle=author_handle,
        author_avatar_url=None if card.is_anonymous else card.author.avatar_url,
        upvotes=card.upvotes,
        downvotes=card.downvotes,
        score=card.score,
        my_vote=my_vote,
        comment_count=comment_count,
        done_at=card.done_at,
        is_mine=viewer_id is not None and card.author_user_id == viewer_id,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def _cards_to_responses(
    db: Session, cards: list[BacklogCard], viewer_id: UUID | None
) -> list[BacklogCardResponse]:
    card_ids = [card.id for card in cards]
    comment_counts = _comment_counts(db, card_ids)
    my_votes = _my_votes(db, card_ids, viewer_id)
    return [
        _card_to_response(
            card,
            my_vote=my_votes.get(card.id, 0),
            comment_count=comment_counts.get(card.id, 0),
            viewer_id=viewer_id,
        )
        for card in cards
    ]


def list_cards(
    db: Session,
    *,
    viewer_id: UUID | None,
    type_: BacklogCardType | None = None,
    category: str | None = None,
    status: BacklogCardStatus | None = None,
) -> list[BacklogCardResponse]:
    query = select(BacklogCard).options(joinedload(BacklogCard.author))
    if type_ is not None:
        query = query.where(BacklogCard.type == type_)
    if category is not None:
        query = query.where(BacklogCard.category == category)
    if status is not None:
        query = query.where(BacklogCard.status == status)
    query = query.order_by(BacklogCard.score.desc(), BacklogCard.created_at.desc())
    cards = list(db.execute(query).unique().scalars().all())
    return _cards_to_responses(db, cards, viewer_id)


def get_card(db: Session, card_id: UUID, *, viewer_id: UUID | None) -> BacklogCardResponse:
    card = _get_card(db, card_id, for_update_author=True)
    return _cards_to_responses(db, [card], viewer_id)[0]


def create_card(
    db: Session,
    *,
    author_id: UUID,
    type_: BacklogCardType,
    category: str,
    title: str,
    description: str,
    is_anonymous: bool,
) -> BacklogCardResponse:
    title = title.strip()
    description = description.strip()
    if not title:
        raise BacklogError("Заголовок не может быть пустым")
    if not description:
        raise BacklogError("Описание не может быть пустым")
    if category not in BACKLOG_CATEGORY_KEYS:
        raise BacklogError("Неизвестная категория")

    card = BacklogCard(
        type=type_,
        category=category,
        title=title,
        description=description,
        is_anonymous=_resolve_anonymous(db, author_id, is_anonymous),
        author_user_id=author_id,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    card = _get_card(db, card.id, for_update_author=True)
    _notify_new_card(card)
    return _cards_to_responses(db, [card], author_id)[0]


def _apply_card_update(
    db: Session,
    card_id: UUID,
    *,
    editor: User,
    type_: BacklogCardType | None,
    category: str | None,
    title: str | None,
    description: str | None,
    is_anonymous: bool | None,
    status: BacklogCardStatus | None,
) -> BacklogCard:
    """Редактирование карточки автором или админом. Возвращает свежую карточку.

    Права: админ правит любую, обычный пользователь — только свою. status
    (модерация) доступен только админу — у автора попытка его сменить даёт 403,
    а не тихо игнорируется, чтобы клиент не думал, что смена прошла.
    """
    admin = is_admin_user(editor, get_settings())
    card = _get_card(db, card_id, for_update_author=True)
    if not admin and card.author_user_id != editor.id:
        raise BacklogError("Редактировать можно только свою карточку", status_code=403)

    if type_ is not None:
        card.type = type_
    if category is not None:
        if category not in BACKLOG_CATEGORY_KEYS:
            raise BacklogError("Неизвестная категория")
        card.category = category
    if title is not None:
        title = title.strip()
        if not title:
            raise BacklogError("Заголовок не может быть пустым")
        card.title = title
    if description is not None:
        description = description.strip()
        if not description:
            raise BacklogError("Описание не может быть пустым")
        card.description = description
    if is_anonymous is not None:
        # _resolve_anonymous снимет анонимность, если карточку ведёт админ.
        card.is_anonymous = _resolve_anonymous(db, card.author_user_id, is_anonymous)
    if status is not None:
        if not admin:
            raise BacklogError("Статус карточки меняет только администратор", status_code=403)
        card.status = status

    db.commit()
    return _get_card(db, card_id, for_update_author=True)


def update_card(
    db: Session,
    card_id: UUID,
    *,
    editor: User,
    type_: BacklogCardType | None = None,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    is_anonymous: bool | None = None,
    status: BacklogCardStatus | None = None,
) -> BacklogCardResponse:
    """Публичное редактирование (автор своей карточки). Отдаёт витринную форму."""
    card = _apply_card_update(
        db,
        card_id,
        editor=editor,
        type_=type_,
        category=category,
        title=title,
        description=description,
        is_anonymous=is_anonymous,
        status=status,
    )
    return _cards_to_responses(db, [card], editor.id)[0]


def update_card_admin(
    db: Session,
    card_id: UUID,
    *,
    editor: User,
    type_: BacklogCardType | None = None,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    is_anonymous: bool | None = None,
    status: BacklogCardStatus | None = None,
) -> BacklogCardAdminResponse:
    """Админское редактирование (любая карточка). Отдаёт админскую форму."""
    card = _apply_card_update(
        db,
        card_id,
        editor=editor,
        type_=type_,
        category=category,
        title=title,
        description=description,
        is_anonymous=is_anonymous,
        status=status,
    )
    comment_count = _comment_counts(db, [card.id]).get(card.id, 0)
    return _card_to_admin_response(card, comment_count=comment_count)


def _recompute_vote_counts(db: Session, card: BacklogCard) -> None:
    upvotes = db.execute(
        select(func.count()).select_from(BacklogVote).where(BacklogVote.card_id == card.id, BacklogVote.value == 1)
    ).scalar_one()
    downvotes = db.execute(
        select(func.count()).select_from(BacklogVote).where(BacklogVote.card_id == card.id, BacklogVote.value == -1)
    ).scalar_one()
    card.upvotes = int(upvotes)
    card.downvotes = int(downvotes)
    card.score = card.upvotes - card.downvotes


def vote_card(db: Session, card_id: UUID, *, user_id: UUID, value: int) -> BacklogCardResponse:
    card = _get_card(db, card_id)
    # Реализованное уже не приоритизируют — голоса на такой карточке ничего не
    # решают. Обсуждение при этом остаётся открытым.
    if card.status is BacklogCardStatus.done:
        raise BacklogError("Фича уже реализована — голосование закрыто")
    existing = db.get(BacklogVote, (card_id, user_id))
    had_vote_before = existing is not None

    if value == 0 or (existing is not None and existing.value == value):
        if existing is not None:
            db.delete(existing)
    elif existing is not None:
        existing.value = value
    else:
        db.add(BacklogVote(card_id=card_id, user_id=user_id, value=value))

    db.flush()
    _recompute_vote_counts(db, card)
    db.commit()
    card = _get_card(db, card_id, for_update_author=True)

    if not had_vote_before and value != 0:
        _notify_new_vote(card, value)

    return _cards_to_responses(db, [card], user_id)[0]


def list_comments(db: Session, card_id: UUID, *, viewer_id: UUID | None = None) -> list[BacklogCommentResponse]:
    _get_card(db, card_id)
    rows = list(
        db.execute(
            select(BacklogComment)
            .options(joinedload(BacklogComment.author))
            .where(BacklogComment.card_id == card_id)
            .order_by(BacklogComment.created_at.asc())
        )
        .unique()
        .scalars()
        .all()
    )
    return [_comment_to_response(comment, viewer_id=viewer_id) for comment in rows]


def _is_admin_author(comment: BacklogComment) -> bool:
    """Метка «Админ» на реплике администратора сайта.

    Анонимность админу недоступна (см. _resolve_anonymous), так что метка всегда
    идёт вместе с настоящим именем — читатель понимает, что это официальный
    ответ разработчика.
    """
    return is_admin_user(comment.author, get_settings())


def _resolve_anonymous(db: Session, author_id: UUID, requested: bool) -> bool:
    """Админ пишет только под своим именем — анонимность для него отключена.

    Иначе ответ разработчика выглядел бы как «Аноним» с плашкой «Админ», что
    только путает читателей.
    """
    if not requested:
        return False
    author = db.get(User, author_id)
    if author is not None and is_admin_user(author, get_settings()):
        return False
    return True


def _comment_to_response(comment: BacklogComment, *, viewer_id: UUID | None = None) -> BacklogCommentResponse:
    author_name, author_handle = (None, None) if comment.is_anonymous else _author_display(comment.author)
    author_avatar = None if comment.is_anonymous else comment.author.avatar_url
    return BacklogCommentResponse(
        id=comment.id,
        card_id=comment.card_id,
        body=comment.body,
        is_anonymous=comment.is_anonymous,
        author_display_name=author_name,
        author_handle=author_handle,
        author_avatar_url=author_avatar,
        is_admin_author=_is_admin_author(comment),
        is_mine=viewer_id is not None and comment.author_user_id == viewer_id,
        created_at=comment.created_at,
    )


def create_comment(
    db: Session, card_id: UUID, *, author_id: UUID, body: str, is_anonymous: bool
) -> BacklogCommentResponse:
    card = _get_card(db, card_id, for_update_author=True)
    body = body.strip()
    if not body:
        raise BacklogError("Комментарий не может быть пустым")

    comment = BacklogComment(
        card_id=card_id,
        author_user_id=author_id,
        body=body,
        is_anonymous=_resolve_anonymous(db, author_id, is_anonymous),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    comment = db.execute(
        select(BacklogComment).options(joinedload(BacklogComment.author)).where(BacklogComment.id == comment.id)
    ).scalar_one()
    _notify_new_comment(card, comment)
    return _comment_to_response(comment, viewer_id=author_id)


def _card_to_admin_response(card: BacklogCard, *, comment_count: int) -> BacklogCardAdminResponse:
    author_name, author_handle = _author_display(card.author)
    return BacklogCardAdminResponse(
        id=card.id,
        type=card.type,
        category=card.category,
        title=card.title,
        description=card.description,
        status=card.status,
        is_anonymous=card.is_anonymous,
        author_display_name=author_name,
        author_handle=author_handle,
        author_avatar_url=None if card.is_anonymous else card.author.avatar_url,
        upvotes=card.upvotes,
        downvotes=card.downvotes,
        score=card.score,
        comment_count=comment_count,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def list_cards_admin(db: Session) -> list[BacklogCardAdminResponse]:
    # Реализованное опускаем в подвал (done_last=1), остальное по голосам —
    # админ модерирует активную часть бэклога, готовое не мешается сверху.
    done_last = (BacklogCard.status == BacklogCardStatus.done).cast(Integer)
    cards = list(
        db.execute(
            select(BacklogCard)
            .options(joinedload(BacklogCard.author))
            .order_by(done_last.asc(), BacklogCard.score.desc(), BacklogCard.created_at.desc())
        )
        .unique()
        .scalars()
        .all()
    )
    comment_counts = _comment_counts(db, [card.id for card in cards])
    return [_card_to_admin_response(card, comment_count=comment_counts.get(card.id, 0)) for card in cards]


def update_card_status(db: Session, card_id: UUID, *, status: BacklogCardStatus) -> BacklogCardAdminResponse:
    card = _get_card(db, card_id, for_update_author=True)
    # Момент перевода в «реализовано» фиксируем отдельно: лента показывает
    # возраст карточки, а у сделанного полезнее «реализовано N назад».
    # Повторный перевод в done обновляет отметку, уход из done — снимает.
    if status == BacklogCardStatus.done:
        card.done_at = datetime.now(UTC)
    elif card.status == BacklogCardStatus.done:
        card.done_at = None
    card.status = status
    db.commit()
    db.refresh(card)
    card = _get_card(db, card_id, for_update_author=True)
    comment_count = _comment_counts(db, [card.id]).get(card.id, 0)
    return _card_to_admin_response(card, comment_count=comment_count)


def list_card_votes_admin(db: Session, card_id: UUID) -> BacklogVoteAdminListResponse:
    """Кто как проголосовал — голосование не анонимно, автор всегда виден."""
    _get_card(db, card_id)
    rows = list(
        db.execute(
            select(BacklogVote)
            .options(joinedload(BacklogVote.user))
            .where(BacklogVote.card_id == card_id)
            .order_by(BacklogVote.created_at.asc())
        )
        .unique()
        .scalars()
        .all()
    )
    up: list[BacklogVoteAdminItem] = []
    down: list[BacklogVoteAdminItem] = []
    for vote in rows:
        name, handle = _author_display(vote.user)
        item = BacklogVoteAdminItem(
            value=vote.value, author_display_name=name, author_handle=handle, created_at=vote.created_at
        )
        (up if vote.value > 0 else down).append(item)
    return BacklogVoteAdminListResponse(upvotes=up, downvotes=down)


def delete_card(db: Session, card_id: UUID) -> None:
    card = _get_card(db, card_id)
    db.delete(card)
    db.commit()


def delete_comment(db: Session, comment_id: UUID) -> None:
    comment = db.get(BacklogComment, comment_id)
    if comment is None:
        raise BacklogError("Комментарий не найден", status_code=404)
    db.delete(comment)
    db.commit()
