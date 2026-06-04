from app.parkrun.fetch.captcha_wait import is_parkrun_page_ready, page_url_matches_target


def test_page_url_matches_target_normalizes_trailing_slash() -> None:
    a = "https://www.parkrun.org.uk/parkrunner/1241123/all/"
    b = "https://www.parkrun.org.uk/parkrunner/1241123/all"
    assert page_url_matches_target(a, b)


def test_page_url_matches_target_different_paths() -> None:
    summary = "https://www.parkrun.org.uk/parkrunner/1241123/"
    all_results = "https://www.parkrun.org.uk/parkrunner/1241123/all/"
    assert not page_url_matches_target(summary, all_results)


def test_is_parkrun_page_ready_rejects_human_verification_title() -> None:
    html = "<html><body>ok</body></html>" * 50
    assert not is_parkrun_page_ready(
        title="Human Verification",
        html=html,
        url="https://www.parkrun.org.uk/parkrunner/1/all/",
    )


def test_is_parkrun_page_ready_accepts_normal_results_page() -> None:
    html = (
        "<html><head><title>results | parkrun UK</title></head>"
        "<body><h1>parkrunner</h1><table><tr><td>parkrun</td></tr></table></body></html>"
    ) * 20
    assert is_parkrun_page_ready(
        title="results | parkrun UK",
        html=html,
        url="https://www.parkrun.org.uk/parkrunner/1/all/",
    )
