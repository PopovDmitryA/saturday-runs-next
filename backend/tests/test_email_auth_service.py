from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.signup_guard import SignupContext
from app.models import AuthIdentity, AuthProvider, User
from app.services import email_auth_service
from app.services.auth_service import AuthError


@pytest.fixture
def settings() -> Settings:
    return Settings(
        email_login_enabled=True,
        smtp_enabled=True,
        smtp_host="smtp.timeweb.ru",
        smtp_user="support@run5k.run",
        smtp_password="secret",
    )


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Перехватываем письма: (кому, текст)."""
    outbox: list[tuple[str, str]] = []

    def fake_queue(to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
        outbox.append((to, text_body))
        return True

    monkeypatch.setattr("app.workers.tasks.email_send.queue_email", fake_queue)
    return outbox


def _code_from(outbox: list[tuple[str, str]]) -> str:
    text = outbox[-1][1]
    digits = [word for word in text.replace("\n", " ").split() if word.isdigit() and len(word) == 6]
    return digits[0]


def _make_user(db: Session, *, provider: AuthProvider, external_id: str, email: str | None) -> User:
    user = User(display_name="Бегун", consent_accepted=True)
    db.add(user)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=provider,
            external_id=external_id,
            display_name="Бегун",
            email=email,
            profile_json={},
        )
    )
    db.commit()
    return user


def test_code_is_sent_and_lets_new_person_in(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"

    result = email_auth_service.request_code(
        db_session, settings, email, client_ip="10.0.0.1", consent=True
    )

    assert result["expires_in"] == settings.email_login_code_ttl_seconds
    assert sent_codes[-1][0] == email

    user_id = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))
    assert user_id is not None


def test_same_person_returns_to_the_same_account(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    first = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    second = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    assert first == second


def test_alias_of_the_same_mailbox_is_the_same_account(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """`ivan+run@` и `ivan@` — один ящик, значит и профиль один."""
    handle = f"runner-{uuid4().hex[:8]}"

    email_auth_service.request_code(
        db_session, settings, f"{handle}@example.com", client_ip="10.0.0.1", consent=True
    )
    first = email_auth_service.verify_code(
        db_session, settings, f"{handle}@example.com", _code_from(sent_codes)
    )

    email_auth_service.request_code(
        db_session, settings, f"{handle}+run@example.com", client_ip="10.0.0.2", consent=True
    )
    second = email_auth_service.verify_code(
        db_session, settings, f"{handle}+run@example.com", _code_from(sent_codes)
    )

    assert first == second


def test_yandex_account_is_reused_instead_of_a_second_one(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Человек входил через Яндекс, теперь по коду — это один и тот же профиль."""
    handle = f"runner-{uuid4().hex[:8]}"
    yandex_user = _make_user(
        db_session,
        provider=AuthProvider.yandex,
        external_id=uuid4().hex[:12],
        email=f"{handle}@yandex.ru",
    )

    # Заходит по ya.ru с точкой в имени — у Яндекса это тот же ящик.
    login = f"{handle.replace('-', '.')}@ya.ru"
    email_auth_service.request_code(db_session, settings, login, client_ip="10.0.0.1", consent=True)
    user_id = email_auth_service.verify_code(db_session, settings, login, _code_from(sent_codes))

    assert user_id == yandex_user.id


def test_wrong_code_is_rejected_and_attempts_run_out(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)

    for _ in range(settings.email_login_max_attempts - 1):
        with pytest.raises(AuthError) as first_errors:
            email_auth_service.verify_code(db_session, settings, email, "000000")
        assert first_errors.value.status_code == 400

    # Последняя попытка сжигает код целиком: дальше только новый запрос.
    with pytest.raises(AuthError) as exc:
        email_auth_service.verify_code(db_session, settings, email, "000000")
    assert exc.value.status_code == 429

    with pytest.raises(AuthError):
        email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))


def test_previous_code_still_works_after_a_second_request(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Почта приходит с задержкой: человек берёт цифры из первого письма."""
    email = f"runner-{uuid4().hex[:8]}@example.com"

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    first_code = _code_from(sent_codes)
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    second_code = _code_from(sent_codes)
    assert first_code != second_code

    user_id = email_auth_service.verify_code(db_session, settings, email, first_code)
    assert user_id is not None


def test_successful_login_burns_every_outstanding_code(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Вошёл по одному — остальные письма превращаются в тыкву."""
    email = f"runner-{uuid4().hex[:8]}@example.com"

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    first_code = _code_from(sent_codes)
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    second_code = _code_from(sent_codes)

    email_auth_service.verify_code(db_session, settings, email, second_code)
    with pytest.raises(AuthError):
        email_auth_service.verify_code(db_session, settings, email, first_code)


def test_only_the_last_few_codes_stay_alive(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Пачка живых ключей от профиля ни к чему: держим только последние."""
    limited = settings.model_copy(update={"email_login_active_codes": 2})
    email = f"runner-{uuid4().hex[:8]}@example.com"

    email_auth_service.request_code(db_session, limited, email, client_ip="10.0.0.1", consent=True)
    oldest = _code_from(sent_codes)
    for _ in range(2):
        email_auth_service.request_code(
            db_session, limited, email, client_ip="10.0.0.1", consent=True
        )
    newest = _code_from(sent_codes)

    with pytest.raises(AuthError):
        email_auth_service.verify_code(db_session, limited, email, oldest)
    assert email_auth_service.verify_code(db_session, limited, email, newest) is not None


def test_code_is_single_use(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    code = _code_from(sent_codes)

    email_auth_service.verify_code(db_session, settings, email, code)
    with pytest.raises(AuthError):
        email_auth_service.verify_code(db_session, settings, email, code)


def test_news_consent_subscribes_a_new_person(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(
        db_session, settings, email, client_ip="10.0.0.1", consent=True, news_consent=True
    )
    user_id = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    user = db_session.query(User).filter(User.id == user_id).one()
    assert user.news_subscribed


def test_login_without_the_news_box_does_not_subscribe(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    user_id = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    user = db_session.query(User).filter(User.id == user_id).one()
    assert not user.news_subscribed


def test_login_never_silently_unsubscribes(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Снятая галочка на входе — это «не отмечал сейчас», а не «отпишите меня»."""
    email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(
        db_session, settings, email, client_ip="10.0.0.1", consent=True, news_consent=True
    )
    user_id = email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    email_auth_service.request_code(db_session, settings, email, client_ip="10.0.0.1", consent=True)
    email_auth_service.verify_code(db_session, settings, email, _code_from(sent_codes))

    user = db_session.query(User).filter(User.id == user_id).one()
    assert user.news_subscribed


def test_disposable_mailbox_is_refused(db_session: Session, settings: Settings) -> None:
    with pytest.raises(AuthError) as exc:
        email_auth_service.request_code(
            db_session, settings, "someone@mailinator.com", client_ip="10.0.0.1", consent=True
        )
    assert exc.value.status_code == 400


def test_malformed_address_is_refused(db_session: Session, settings: Settings) -> None:
    with pytest.raises(AuthError):
        email_auth_service.request_code(
            db_session, settings, "не-почта", client_ip="10.0.0.1", consent=True
        )


def test_repeated_requests_to_one_address_are_throttled(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Чужой ящик нельзя завалить письмами, вписывая его адрес снова и снова."""
    email = f"runner-{uuid4().hex[:8]}@example.com"
    for _ in range(settings.email_login_code_per_address):
        email_auth_service.request_code(
            db_session, settings, email, client_ip="10.0.0.1", consent=True
        )

    with pytest.raises(AuthError) as exc:
        email_auth_service.request_code(
            db_session, settings, email, client_ip="10.0.0.1", consent=True
        )
    assert exc.value.status_code == 429


def test_signup_limit_applies_to_email_registrations(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Лимит новых профилей не обходится сменой способа входа."""
    limited = settings.model_copy(update={"signup_limit_per_device_daily": 1})
    context = SignupContext(ip="10.0.0.9", device_id="device-one")

    first_email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(
        db_session, limited, first_email, client_ip="10.0.0.9", consent=True
    )
    email_auth_service.verify_code(
        db_session, limited, first_email, _code_from(sent_codes), signup_context=context
    )

    second_email = f"runner-{uuid4().hex[:8]}@example.com"
    email_auth_service.request_code(
        db_session, limited, second_email, client_ip="10.0.0.9", consent=True
    )
    with pytest.raises(AuthError) as exc:
        email_auth_service.verify_code(
            db_session, limited, second_email, _code_from(sent_codes), signup_context=context
        )
    assert exc.value.status_code == 429


def test_daily_quota_stops_a_flood_from_many_addresses(
    db_session: Session,
    settings: Settings,
    sent_codes: list[tuple[str, str]],
) -> None:
    """Вёдра по адресу и IP держат одного обидчика, суточный потолок — толпу."""
    capped = settings.model_copy(update={"email_login_codes_per_day": 2})

    for index in range(2):
        email_auth_service.request_code(
            db_session, capped, f"flood-{index}@example.com", client_ip=f"10.1.0.{index}", consent=True
        )

    with pytest.raises(AuthError) as exc:
        email_auth_service.request_code(
            db_session, capped, "flood-3@example.com", client_ip="10.1.0.3", consent=True
        )
    # 503, а не 429: это не «вы слишком часто», а «способ временно недоступен,
    # войдите через VK или Яндекс».
    assert exc.value.status_code == 503


def test_login_is_refused_when_mailer_is_not_configured(
    db_session: Session, settings: Settings
) -> None:
    with pytest.raises(AuthError) as exc:
        email_auth_service.request_code(
            db_session,
            settings.model_copy(update={"smtp_password": ""}),
            "runner@example.com",
            client_ip="10.0.0.1",
            consent=True,
        )
    assert exc.value.status_code == 503
