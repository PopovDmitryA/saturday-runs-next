from __future__ import annotations

import pytest

from app.models import User
from app.services.user_history_milestone_service import (
    UnknownMilestoneKindError,
    list_user_milestone_kind_settings,
    set_user_disabled_milestone_kinds,
)


def test_bulk_set_replaces_disabled_kinds_in_canonical_order() -> None:
    user = User(history_disabled_kinds=["pr"])
    set_user_disabled_milestone_kinds(user, ["new_city", "global_pr", "new_city"])
    # Прежний «pr» не в списке — снова показывается; дубли схлопнуты.
    disabled = {row["kind"] for row in list_user_milestone_kind_settings(user) if not row["enabled"]}
    assert disabled == {"new_city", "global_pr"}
    assert len(user.history_disabled_kinds) == 2


def test_bulk_set_empty_list_shows_everything() -> None:
    user = User(history_disabled_kinds=["pr", "new_city"])
    set_user_disabled_milestone_kinds(user, [])
    assert user.history_disabled_kinds == []


def test_bulk_set_rejects_unknown_kind_without_changes() -> None:
    user = User(history_disabled_kinds=["pr"])
    with pytest.raises(UnknownMilestoneKindError):
        set_user_disabled_milestone_kinds(user, ["pr", "no_such_kind"])
    assert user.history_disabled_kinds == ["pr"]
