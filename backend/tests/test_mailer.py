from __future__ import annotations

import smtplib

import pytest

from app.config import Settings
from app.core import mailer


@pytest.fixture
def settings() -> Settings:
    return Settings(
        smtp_enabled=True,
        smtp_host="smtp.timeweb.ru",
        smtp_port=465,
        smtp_user="support@run5k.run",
        smtp_password="secret",
        smtp_from_name="run5k.run",
    )


def test_sender_defaults_to_login_when_from_is_empty(settings: Settings) -> None:
    """Пока ящик один, отдельный адрес отправителя задавать не нужно."""
    assert mailer.sender_email(settings) == "support@run5k.run"
    assert mailer.reply_to_email(settings) == "support@run5k.run"


def test_sender_can_be_split_from_login(settings: Settings) -> None:
    """Появится второй ящик — разводится настройками, без правки кода."""
    split = settings.model_copy(
        update={"smtp_from_email": "login@run5k.run", "smtp_reply_to": "support@run5k.run"}
    )
    assert mailer.sender_email(split) == "login@run5k.run"
    assert mailer.reply_to_email(split) == "support@run5k.run"


def test_not_configured_without_password(settings: Settings) -> None:
    assert not mailer.is_configured(settings.model_copy(update={"smtp_password": ""}))


def test_not_configured_when_disabled(settings: Settings) -> None:
    assert not mailer.is_configured(settings.model_copy(update={"smtp_enabled": False}))


def test_message_headers(settings: Settings) -> None:
    message = mailer.build_message(
        settings,
        to="runner@example.com",
        subject="Код для входа",
        text_body="Ваш код: 123456",
    )

    # Точка в имени — спецсимвол по RFC 5322, поэтому оно уезжает в кавычки.
    assert message["From"] == '"run5k.run" <support@run5k.run>'
    assert message["To"] == "runner@example.com"
    assert message["Subject"] == "Код для входа"
    # Date и Message-ID проставляем сами: без них приёмники штрафуют письмо.
    assert message["Date"]
    assert message["Message-ID"].endswith("@run5k.run>")
    # Reply-To не дублируем, когда он совпадает с отправителем.
    assert message["Reply-To"] is None


def test_reply_to_is_set_when_it_differs(settings: Settings) -> None:
    split = settings.model_copy(
        update={"smtp_from_email": "login@run5k.run", "smtp_reply_to": "support@run5k.run"}
    )
    message = mailer.build_message(split, to="runner@example.com", subject="s", text_body="t")
    assert message["Reply-To"] == "support@run5k.run"


def test_html_is_added_as_alternative(settings: Settings) -> None:
    """Текстовая версия обязательна: по ней письмо читают фильтры и простые клиенты."""
    message = mailer.build_message(
        settings,
        to="runner@example.com",
        subject="s",
        text_body="Ваш код: 123456",
        html_body="<p>Ваш код: <b>123456</b></p>",
    )

    assert message.is_multipart()
    subtypes = {part.get_content_subtype() for part in message.iter_parts()}
    assert subtypes == {"plain", "html"}


def test_send_refuses_without_configuration(settings: Settings) -> None:
    with pytest.raises(mailer.MailerError):
        mailer.send_email(
            settings.model_copy(update={"smtp_password": ""}),
            to="runner@example.com",
            subject="s",
            text_body="t",
        )


def test_send_logs_in_and_delivers(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    sent: dict[str, object] = {}

    class FakeSMTP:
        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def login(self, user: str, password: str) -> None:
            sent["login"] = (user, password)

        def send_message(self, message: object) -> None:
            sent["message"] = message

    monkeypatch.setattr(mailer, "_connect", lambda _settings: FakeSMTP())

    mailer.send_email(settings, to="runner@example.com", subject="Код", text_body="123456")

    assert sent["login"] == ("support@run5k.run", "secret")
    assert sent["message"]["To"] == "runner@example.com"


def test_network_failure_becomes_mailer_error(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Закрытый исходящий порт выглядит как OSError — вызывающему это тот же исход."""

    def boom(_settings: Settings) -> None:
        raise OSError("Connection refused")

    monkeypatch.setattr(mailer, "_connect", boom)

    with pytest.raises(mailer.MailerError):
        mailer.send_email(settings, to="runner@example.com", subject="s", text_body="t")


def test_smtp_failure_becomes_mailer_error(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    def boom(_settings: Settings) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(mailer, "_connect", boom)

    with pytest.raises(mailer.MailerError):
        mailer.send_email(settings, to="runner@example.com", subject="s", text_body="t")
