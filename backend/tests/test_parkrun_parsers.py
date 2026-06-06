from app.parkrun.parsers.athlete import parse_all_results_html, parse_profile_summary_html
from app.platform_adapters.parkrun.url import parse_profile_url

SAMPLE_ALL_HEAD = """
<html><body>
<h2>Дмитрий ПОПОВ (A7035519)</h2>
<h3>24 parkruns total</h3>
<p>Most recent age category was SM25-29</p>
<table>
<caption>All  Results</caption>
<tr><th>Event</th><th>Run Date</th><th>Run Number</th><th>Pos</th><th>Time</th><th>Age Grade</th><th>PB?</th></tr>
<tr><td>Timiryazevsky</td><td>26/02/2022</td><td>283</td><td>13</td><td>25:30</td><td>50.59%</td><td>★</td></tr>
</table>
<h3>Volunteer Summary</h3>
<table>
<tr><th>Role</th><th>Occasions</th></tr>
<tr><td>Run Director</td><td>2</td></tr>
</table>
</body></html>
"""


def test_parse_all_results_table() -> None:
    parsed = parse_profile_url("https://www.parkrun.org.uk/parkrunner/7035519/all/")
    participant, runs, extra = parse_all_results_html(SAMPLE_ALL_HEAD, parsed=parsed)
    assert participant.display_name == "Дмитрий ПОПОВ"
    assert participant.barcode_id == "A7035519"
    assert participant.total_runs == 24
    assert extra["age_category"] == "SM25-29"
    assert len(runs) == 1
    assert runs[0].finish_time_display == "00:25:30"
    assert runs[0].is_pr is False
    assert extra["volunteer_occasions_total"] == 2


def test_parse_profile_volunteer_summary() -> None:
    parsed = parse_profile_url("https://www.parkrun.org.uk/parkrunner/7035519/")
    participant, extra = parse_profile_summary_html(SAMPLE_ALL_HEAD, parsed=parsed)
    assert participant.external_user_id == "7035519"
    assert extra["volunteer_summary"][0]["role"] == "Run Director"
