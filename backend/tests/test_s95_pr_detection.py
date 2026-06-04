from __future__ import annotations

from bs4 import BeautifulSoup

from app.s95.parsers.pr_detection import row_is_pr


def test_row_is_pr_fa_bolt_title() -> None:
    html = (
        '<tr><td>2</td><td><a href="/athletes/12834">Наталья</a>'
        '<i class="fa-solid fa-bolt text-primary" title="Личный рекорд!"></i></td><td>21:39</td></tr>'
    )
    row = BeautifulSoup(html, "html.parser").find("tr")
    assert row is not None
    assert row_is_pr(row) is True


def test_row_is_pr_without_marker() -> None:
    html = '<tr><td>3</td><td><a href="/athletes/1">Иван</a></td><td>22:00</td></tr>'
    row = BeautifulSoup(html, "html.parser").find("tr")
    assert row is not None
    assert row_is_pr(row) is False
