from app.parkrun.volunteer_credits import (
    resolve_parkrun_volunteering_count,
    volunteer_credits_from_profile_extra,
    volunteer_credits_from_role_labels,
)


def test_volunteer_credits_from_profile_extra_prefers_total_credits() -> None:
    assert (
        volunteer_credits_from_profile_extra(
            {
                "volunteer_occasions_total": 78,
                "volunteer_summary": [{"role": "Run Director", "occasions": 13}],
            }
        )
        == 78
    )


def test_volunteer_credits_from_role_labels_sums_occasions() -> None:
    assert volunteer_credits_from_role_labels(
        ["Run Director (13×)", "Timekeeper (38×)", "Marshal (1×)"]
    ) == 52


def test_resolve_parkrun_volunteering_count_uses_profile_extra_first() -> None:
    assert (
        resolve_parkrun_volunteering_count(
            profile_extra={"volunteer_occasions_total": 78},
            summary_role_labels=["Run Director (13×)"],
        )
        == 78
    )


def test_resolve_parkrun_volunteering_count_falls_back_to_role_labels() -> None:
    assert (
        resolve_parkrun_volunteering_count(
            profile_extra={},
            summary_role_labels=["Run Director (13×)", "Timekeeper (38×)"],
        )
        == 51
    )
