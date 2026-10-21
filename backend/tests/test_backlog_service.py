"""Юнит-тесты бэклога: карточки, голосование, комментарии, анонимность, админ."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import BacklogCard, BacklogCardStatus, BacklogCardType, BacklogComment, BacklogVote, User
from app.services.backlog_service import (
    BacklogError,
    create_card,
    create_comment,
    delete_card,
    delete_comment,
    detach_user_from_backlog,
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


def test_card_and_comment_notify_admin_channel(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бэклог уведомляет через общий admin-канал (Telegram), а не напрямую в ВК."""
    from app.services import backlog_service

    sent: list[str] = []
    monkeypatch.setattr(backlog_service, "notify_admin", sent.append)

    author = _make_user(db_session)
    card = _make_card(db_session, author.id, title="Идея с уведомлением")
    create_comment(db_session, card.id, author_id=author.id, body="Первый", is_anonymous=False)

    assert any("Новая карточка бэклога" in text for text in sent)
    assert any("Комментарий к «Идея с уведомлением»" in text for text in sent)


def test_admin_notify_goes_without_open_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notify_admin — сеть (Telegram/ВК): транзакция к этому моменту отпущена,
    иначе Postgres рвёт соединение через 60 с простоя. expire_on_commit — как
    на проде; ответы API при этом прежние."""
    from app.services import backlog_service

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        backlog_service, "notify_admin", lambda text: calls.append((text, db_session.in_transaction()))
    )
    author = _make_user(db_session, name="Автор идеи")
    fan = _make_user(db_session, name="Фанат")
    db_session.expire_on_commit = True

    card = _make_card(db_session, author.id, title="Идея без транзакции")
    voted = vote_card(db_session, card.id, user_id=fan.id, value=1)
    comment = create_comment(db_session, card.id, author_id=fan.id, body="Поддерживаю", is_anonymous=False)

    assert [in_tx for _, in_tx in calls] == [False, False, False]
    assert "«Идея без транзакции» от Автор идеи" in calls[0][0]
    assert "счёт теперь 1 (1/0)" in calls[1][0]
    assert "от Фанат: Поддерживаю" in calls[2][0]
    assert card.title == "Идея без транзакции" and card.author_display_name == "Автор идеи"
    assert voted.score == 1 and voted.my_vote == 1
    assert comment.body == "Поддерживаю" and comment.author_display_name == "Фанат"


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


# ---------------------------------------------------------------------------
# Автор удалил профиль (миграция 109): карточки и реплики остаются без автора


def _orphan(db: Session, user: User) -> None:
    """Снять автора так же, как это делает удаление профиля."""
    detach_user_from_backlog(db, user.id)
    db.delete(user)
    db.commit()
    db.expire_all()


def test_detach_keeps_content_and_recounts_votes(db_session: Session) -> None:
    leaving = _make_user(db_session, name="Уходящий")
    staying = _make_user(db_session, name="Остающийся")
    own = _make_card(db_session, leaving.id, title="Своя")
    foreign = _make_card(db_session, staying.id, title="Чужая")
    vote_card(db_session, own.id, user_id=staying.id, value=1)
    vote_card(db_session, foreign.id, user_id=leaving.id, value=-1)
    create_comment(db_session, foreign.id, author_id=leaving.id, body="Реплика", is_anonymous=False)

    result = detach_user_from_backlog(db_session, leaving.id)

    assert (result.votes_removed, result.cards_recounted, result.cards_kept, result.comments_kept) == (1, 1, 1, 1)
    db_session.commit()
    foreign_card = db_session.get(BacklogCard, foreign.id)
    assert foreign_card is not None
    assert (foreign_card.upvotes, foreign_card.downvotes, foreign_card.score) == (0, 0, 0)
    own_card = db_session.get(BacklogCard, own.id)
    assert own_card is not None and own_card.author_user_id is None and own_card.score == 1
    assert db_session.query(BacklogVote).filter(BacklogVote.user_id == leaving.id).count() == 0
    comment = db_session.query(BacklogComment).filter(BacklogComment.card_id == foreign.id).one()
    assert comment.author_user_id is None


def test_orphan_card_and_comment_render_as_deleted_profile(db_session: Session) -> None:
    leaving = _make_user(db_session, name="Уходящий")
    reader = _make_user(db_session, name="Читатель")
    card = _make_card(db_session, leaving.id, title="Сирота")
    hidden = _make_card(db_session, leaving.id, title="Анонимная сирота", is_anonymous=True)
    create_comment(db_session, card.id, author_id=leaving.id, body="Открыто", is_anonymous=False)
    create_comment(db_session, card.id, author_id=leaving.id, body="Тайно", is_anonymous=True)
    _orphan(db_session, leaving)

    public = {item.title: item for item in list_cards(db_session, viewer_id=reader.id)}
    orphan = public["Сирота"]
    assert orphan.author_deleted is True
    assert (orphan.author_display_name, orphan.author_handle, orphan.author_avatar_url) == (None, None, None)
    assert orphan.is_mine is False
    # Анонимность скрывает об авторе всё, в том числе то, что его больше нет.
    assert public["Анонимная сирота"].author_deleted is False
    assert get_card(db_session, card.id, viewer_id=None).author_deleted is True

    comments = {item.body: item for item in list_comments(db_session, card.id, viewer_id=reader.id)}
    assert comments["Открыто"].author_deleted is True
    assert comments["Открыто"].author_handle is None
    assert comments["Открыто"].is_admin_author is False
    assert comments["Тайно"].author_deleted is False
    assert comments["Тайно"].author_display_name is None

    admin = {item.title: item for item in list_cards_admin(db_session)}
    assert admin["Сирота"].author_deleted is True
    assert admin["Анонимная сирота"].author_deleted is True
    assert admin["Сирота"].author_display_name is None
    assert hidden.id == admin["Анонимная сирота"].id


def test_orphan_card_moderation_still_works(db_session: Session, admin_settings: Settings) -> None:
    leaving = _make_user(db_session, name="Уходящий")
    stranger = _make_user(db_session, name="Прохожий")
    admin = _make_user(db_session, name="Дмитрий Попов")
    admin.telegram_id = ADMIN_TELEGRAM_ID
    db_session.commit()
    card = _make_card(db_session, leaving.id, title="Сирота")
    _orphan(db_session, leaving)

    # Править чужую (ничью) карточку обычный человек не может.
    with pytest.raises(BacklogError) as exc:
        update_card(db_session, card.id, editor=stranger, title="Моя теперь")
    assert exc.value.status_code == 403

    # Админ меняет статус и анонимность; голос и комментарий к ней работают.
    status_view = update_card_status(db_session, card.id, status=BacklogCardStatus.in_progress)
    assert status_view.status == BacklogCardStatus.in_progress and status_view.author_deleted is True
    edited = update_card_admin(db_session, card.id, editor=admin, is_anonymous=True)
    assert edited.is_anonymous is True
    voted = vote_card(db_session, card.id, user_id=stranger.id, value=1)
    assert voted.score == 1
    reply = create_comment(db_session, card.id, author_id=stranger.id, body="Подхвачу", is_anonymous=False)
    assert reply.author_display_name == "Прохожий"
