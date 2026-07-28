"""Юнит-тесты бэклога: карточки, голосование, комментарии, анонимность, админ."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import BacklogCardStatus, BacklogCardType, User
from app.services.backlog_service import (
    BacklogError,
    create_card,
    create_comment,
    delete_card,
    delete_comment,
    get_card,
    list_card_votes_admin,
    list_cards,
    list_cards_admin,
    list_comments,
    update_card,
    update_card_admin,
    update_card_status,
    vote_card,
)


def _make_user(db: Session, *, name: str = "Тест") -> User:
    user = User(display_name=name)
    db.add(user)
    db.commit()
    return user


def _make_card(db: Session, author_id, *, title: str = "Идея", is_anonymous: bool = False):
    return create_card(
        db,
        author_id=author_id,
        type_=BacklogCardType.feature,
        category="other",
        title=title,
        description="Описание карточки",
        is_anonymous=is_anonymous,
    )


def test_create_card_validation(db_session: Session) -> None:
    author = _make_user(db_session)
    with pytest.raises(BacklogError):
        create_card(
            db_session,
            author_id=author.id,
            type_=BacklogCardType.bug,
            category="other",
            title="  ",
            description="текст",
            is_anonymous=False,
        )
    with pytest.raises(BacklogError):
        create_card(
            db_session,
            author_id=author.id,
            type_=BacklogCardType.bug,
            category="not-a-real-category",
            title="Название",
            description="текст",
            is_anonymous=False,
        )


def test_create_card_anonymous_hides_author(db_session: Session) -> None:
    author = _make_user(db_session, name="Иван")
    public_card = _make_card(db_session, author.id, title="Публичная", is_anonymous=False)
    anon_card = _make_card(db_session, author.id, title="Анонимная", is_anonymous=True)

    assert public_card.author_display_name == "Иван"
    assert anon_card.author_display_name is None
    assert anon_card.author_handle is None


def test_list_cards_ranked_by_score_then_recency(db_session: Session) -> None:
    author = _make_user(db_session)
    voter = _make_user(db_session, name="Голосующий")
    _make_card(db_session, author.id, title="низкий")
    high = _make_card(db_session, author.id, title="высокий")

    vote_card(db_session, high.id, user_id=voter.id, value=1)

    items = list_cards(db_session, viewer_id=author.id)
    titles = [item.title for item in items]
    assert titles.index("высокий") < titles.index("низкий")
    assert items[titles.index("высокий")].score == 1
    assert items[titles.index("низкий")].score == 0


def test_vote_toggle_and_switch(db_session: Session) -> None:
    author = _make_user(db_session)
    voter = _make_user(db_session, name="Голосующий")
    card = _make_card(db_session, author.id)

    upped = vote_card(db_session, card.id, user_id=voter.id, value=1)
    assert (upped.upvotes, upped.downvotes, upped.score, upped.my_vote) == (1, 0, 1, 1)

    # Повторный клик по той же кнопке снимает голос.
    removed = vote_card(db_session, card.id, user_id=voter.id, value=1)
    assert (removed.upvotes, removed.downvotes, removed.score, removed.my_vote) == (0, 0, 0, 0)

    # Голос в другую сторону меняет направление, не суммируется.
    downed = vote_card(db_session, card.id, user_id=voter.id, value=-1)
    assert (downed.upvotes, downed.downvotes, downed.score, downed.my_vote) == (0, 1, -1, -1)

    other_viewer = _make_user(db_session, name="Сторонний")
    seen_by_other = get_card(db_session, card.id, viewer_id=other_viewer.id)
    assert seen_by_other.my_vote == 0
    assert seen_by_other.score == -1


def test_vote_rejected_on_done_card_but_comments_stay_open(db_session: Session) -> None:
    author = _make_user(db_session)
    voter = _make_user(db_session, name="Голосующий")
    card = _make_card(db_session, author.id)
    update_card_status(db_session, card.id, status=BacklogCardStatus.done)

    with pytest.raises(BacklogError):
        vote_card(db_session, card.id, user_id=voter.id, value=1)

    # Обсуждение реализованной фичи остаётся доступным.
    comment = create_comment(db_session, card.id, author_id=voter.id, body="Спасибо!", is_anonymous=False)
    assert comment.body == "Спасибо!"


def test_vote_missing_card_raises(db_session: Session) -> None:
    voter = _make_user(db_session)
    with pytest.raises(BacklogError):
        vote_card(db_session, uuid4(), user_id=voter.id, value=1)


def test_comments_create_list_and_anonymity(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)
    commenter = _make_user(db_session, name="Комментатор")

    with pytest.raises(BacklogError):
        create_comment(db_session, card.id, author_id=commenter.id, body="   ", is_anonymous=False)

    create_comment(db_session, card.id, author_id=commenter.id, body="Первый", is_anonymous=False)
    create_comment(db_session, card.id, author_id=commenter.id, body="Второй, анонимно", is_anonymous=True)

    # created_at делит транзакцию теста (now() постоянен внутри неё), поэтому
    # порядок двух комментариев с одинаковой меткой времени не гарантирован —
    # сравниваем содержимое по телу, а не по позиции в списке.
    comments = list_comments(db_session, card.id)
    by_body = {c.body: c for c in comments}
    assert set(by_body) == {"Первый", "Второй, анонимно"}
    assert by_body["Первый"].author_display_name == "Комментатор"
    assert by_body["Второй, анонимно"].author_display_name is None

    refreshed = get_card(db_session, card.id, viewer_id=author.id)
    assert refreshed.comment_count == 2


def test_admin_sees_real_author_even_when_anonymous(db_session: Session) -> None:
    author = _make_user(db_session, name="Скрытый автор")
    _make_card(db_session, author.id, title="Анонимная карточка", is_anonymous=True)

    admin_items = list_cards_admin(db_session)
    card = next(item for item in admin_items if item.title == "Анонимная карточка")
    assert card.author_display_name == "Скрытый автор"
    assert card.is_anonymous is True


def test_admin_update_status(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)

    updated = update_card_status(db_session, card.id, status=BacklogCardStatus.rejected)
    assert updated.status == BacklogCardStatus.rejected

    public_view = get_card(db_session, card.id, viewer_id=author.id)
    assert public_view.status == BacklogCardStatus.rejected


def test_admin_votes_list_shows_real_voters(db_session: Session) -> None:
    author = _make_user(db_session)
    fan = _make_user(db_session, name="Фанат")
    hater = _make_user(db_session, name="Критик")
    card = _make_card(db_session, author.id)

    vote_card(db_session, card.id, user_id=fan.id, value=1)
    vote_card(db_session, card.id, user_id=hater.id, value=-1)

    votes = list_card_votes_admin(db_session, card.id)
    assert [v.author_display_name for v in votes.upvotes] == ["Фанат"]
    assert [v.author_display_name for v in votes.downvotes] == ["Критик"]


ADMIN_TELEGRAM_ID = 4242


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Считать админом владельца ADMIN_TELEGRAM_ID внутри backlog_service."""
    settings = Settings(admin_telegram_id=ADMIN_TELEGRAM_ID, telegram_admin_chat_id=0, admin_emails="")
    monkeypatch.setattr("app.services.backlog_service.get_settings", lambda: settings)
    return settings


def test_admin_cannot_post_anonymously(db_session: Session, admin_settings: Settings) -> None:
    admin = _make_user(db_session, name="Дмитрий Попов")
    admin.telegram_id = ADMIN_TELEGRAM_ID
    db_session.commit()

    # Запросили анонимность — сервис её снимает: ответ разработчика должен быть
    # подписан именем, иначе плашка «Админ» висела бы на «Анониме».
    card = create_card(
        db_session,
        author_id=admin.id,
        type_=BacklogCardType.feature,
        category="other",
        title="Идея от админа",
        description="текст",
        is_anonymous=True,
    )
    assert card.is_anonymous is False
    assert card.author_display_name == "Дмитрий Попов"

    comment = create_comment(db_session, card.id, author_id=admin.id, body="Ответ", is_anonymous=True)
    assert comment.is_anonymous is False
    assert comment.author_display_name == "Дмитрий Попов"
    assert comment.is_admin_author is True


def test_regular_user_keeps_anonymity_and_is_not_admin(db_session: Session, admin_settings: Settings) -> None:
    author = _make_user(db_session, name="Обычный участник")
    card = _make_card(db_session, author.id)

    comment = create_comment(db_session, card.id, author_id=author.id, body="Аноним", is_anonymous=True)
    assert comment.is_anonymous is True
    assert comment.author_display_name is None
    assert comment.is_admin_author is False


def test_comment_is_mine_only_for_its_author(db_session: Session) -> None:
    author = _make_user(db_session)
    other = _make_user(db_session, name="Другой")
    card = _make_card(db_session, author.id)
    create_comment(db_session, card.id, author_id=author.id, body="Моя реплика", is_anonymous=True)

    mine = list_comments(db_session, card.id, viewer_id=author.id)
    assert [c.is_mine for c in mine] == [True]

    # Анонимность сохраняется: чужой видит реплику, но не как свою и без автора.
    theirs = list_comments(db_session, card.id, viewer_id=other.id)
    assert [c.is_mine for c in theirs] == [False]
    assert theirs[0].author_display_name is None

    anonymous_viewer = list_comments(db_session, card.id)
    assert [c.is_mine for c in anonymous_viewer] == [False]


def test_delete_card_and_comment(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)
    comment = create_comment(db_session, card.id, author_id=author.id, body="Комментарий", is_anonymous=False)

    delete_comment(db_session, comment.id)
    assert list_comments(db_session, card.id) == []

    delete_card(db_session, card.id)
    with pytest.raises(BacklogError):
        get_card(db_session, card.id, viewer_id=author.id)

    with pytest.raises(BacklogError):
        delete_comment(db_session, uuid4())


def test_is_mine_flag(db_session: Session) -> None:
    author = _make_user(db_session)
    other = _make_user(db_session, name="Другой")
    card = _make_card(db_session, author.id)

    assert get_card(db_session, card.id, viewer_id=author.id).is_mine is True
    assert get_card(db_session, card.id, viewer_id=other.id).is_mine is False
    assert get_card(db_session, card.id, viewer_id=None).is_mine is False


def test_author_can_edit_own_card(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)

    updated = update_card(
        db_session,
        card.id,
        editor=author,
        title="Новый заголовок",
        description="Новое описание",
        category="runs",
        type_=BacklogCardType.bug,
    )
    assert updated.title == "Новый заголовок"
    assert updated.description == "Новое описание"
    assert updated.category == "runs"
    assert updated.type == BacklogCardType.bug


def test_non_author_cannot_edit(db_session: Session) -> None:
    author = _make_user(db_session)
    stranger = _make_user(db_session, name="Чужой")
    card = _make_card(db_session, author.id)

    with pytest.raises(BacklogError) as exc:
        update_card(db_session, card.id, editor=stranger, title="Взлом")
    assert exc.value.status_code == 403


def test_author_cannot_change_status(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)

    with pytest.raises(BacklogError) as exc:
        update_card(db_session, card.id, editor=author, status=BacklogCardStatus.done)
    assert exc.value.status_code == 403


def test_edit_validates_category_and_empty_fields(db_session: Session) -> None:
    author = _make_user(db_session)
    card = _make_card(db_session, author.id)

    with pytest.raises(BacklogError):
        update_card(db_session, card.id, editor=author, category="not-a-real-category")
    with pytest.raises(BacklogError):
        update_card(db_session, card.id, editor=author, title="   ")
    with pytest.raises(BacklogError):
        update_card(db_session, card.id, editor=author, description="   ")


def test_admin_can_edit_any_card_including_status(db_session: Session, admin_settings: Settings) -> None:
    author = _make_user(db_session)
    admin = _make_user(db_session, name="Дмитрий Попов")
    admin.telegram_id = ADMIN_TELEGRAM_ID
    db_session.commit()
    card = _make_card(db_session, author.id)

    updated = update_card_admin(
        db_session,
        card.id,
        editor=admin,
        title="Правка администратора",
        status=BacklogCardStatus.in_progress,
    )
    assert updated.title == "Правка администратора"
    assert updated.status == BacklogCardStatus.in_progress
