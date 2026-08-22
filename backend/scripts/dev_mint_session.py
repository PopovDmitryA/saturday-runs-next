"""Dev-only: mint a login session for the site owner's own account on THIS
worktree's isolated Redis, so the share feature can be clicked through in a
browser without going through VK OAuth. Never use against prod."""

from __future__ import annotations

from app.config import get_settings
from app.core.session import create_session
from app.db.session import get_session_factory
from app.models import AuthIdentity, AuthProvider

settings = get_settings()
db = get_session_factory()()

identity = (
    db.query(AuthIdentity)
    .filter(
        AuthIdentity.provider == AuthProvider.telegram,
        AuthIdentity.external_id == str(settings.admin_telegram_id),
    )
    .first()
)
if identity is None:
    raise SystemExit("Owner account (admin_telegram_id) not linked via Telegram in this dev DB")

signed = create_session(settings, str(identity.user_id))
print(f"document.cookie = \"{settings.session_cookie_name}={signed}; path=/; max-age={settings.session_ttl_seconds}\";")
