"""Решатель капчи AWS WAF для httpx-режима демона очереди (Mac).

Идея та же, что у athlete_sweep/waf_solver.py --fast: страницы качает httpx, а
когда прилетает капча/WAF — поднимается headless-браузер, CLIP решает
головоломку, из контекста снимается домен-скоуповый aws-waf-token, и дальше
httpx ходит с этим токеном (пока WAF не попросит новый). Так очередь сайта
run5k.run обрабатывается на скорости httpx, но без ручного прохождения капчи.

Сам решатель (CLIP-модель + логика капчи) живёт в соседнем проекте
parkrun-monitoring и переиспользуется отсюда — дублировать его в бэкенд смысла
нет. Если проект не склонирован или нет ML-зависимостей (torch/open_clip),
харвестер деградирует мягко: harvest() вернёт None, вызывающий сам решит, что
делать (обычно — остановить пачку, как и раньше при --no-browser).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _monitoring_dir() -> Path | None:
    raw = os.environ.get("PARKRUN_MONITORING_DIR") or str(
        Path.home() / "Projects" / "parkrun-monitoring"
    )
    path = Path(raw)
    return path if (path / "athlete_sweep" / "waf_solver.py").exists() else None


class WafTokenHarvester:
    """Ленивый браузерный решатель: грузит CLIP при первой капче, переиспользует.

    Гонка by design: после решённой капчи страница сама перенавигируется, и
    чтение контента в этот момент бросает исключение — это признак УСПЕХА, надо
    просто проверить куки (см. pass_captcha в parkrun-monitoring)."""

    def __init__(self, user_agent: str) -> None:
        self._ua = user_agent
        self._clip = None
        self._round = 0
        self._playwright = None
        self._solver = None  # модуль athlete_sweep.waf_solver

    @staticmethod
    def available() -> bool:
        return _monitoring_dir() is not None

    def _ensure(self):
        if self._solver is None:
            mon = _monitoring_dir()
            if mon is None:
                raise RuntimeError(
                    "parkrun-monitoring не найден — не могу решать капчу. "
                    "Склонируй его в ~/Projects/ или задай PARKRUN_MONITORING_DIR."
                )
            if str(mon) not in sys.path:
                sys.path.insert(0, str(mon))
            from athlete_sweep import waf_solver  # noqa: PLC0415

            self._solver = waf_solver
        if self._clip is None:
            from app.parkrun.fetch.daemon_log import cline  # noqa: PLC0415

            cline("Загружаю CLIP (первая капча в этом прогоне)…")
            self._clip = self._solver.Clip()
            self._round = self._solver.next_round_no()
        if self._playwright is None:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415

            self._playwright = sync_playwright().start()
        return self._solver

    def close(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def harvest(self, url: str) -> tuple[str | None, bool]:
        """Зайти браузером на url, решить капчу если есть, снять aws-waf-token.

        Возвращает (token, cleared):
        - token — свежий aws-waf-token, если WAF его выдал;
        - cleared — браузер увидел НОРМАЛЬНУЮ страницу (капчи нет).

        Токен и «чисто» — разные вещи: когда WAF пропускает браузер без
        челленджа, куки aws-waf-token просто не появляется. Раньше это
        считалось провалом и роняло всю пачку, хотя защита уже снялась и
        httpx мог спокойно продолжать.
        """
        try:
            solver = self._ensure()
        except Exception as exc:
            logger.warning("captcha solver unavailable: %r", exc)
            return None, False

        br = self._playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = br.new_context(user_agent=self._ua, viewport={"width": 1280, "height": 900})
        token = None
        cleared = False
        try:
            pg = ctx.new_page()
            # WAF под нагрузкой рвёт соединения браузера (ERR_CONNECTION_CLOSED),
            # хотя через минуту тот же адрес открывается. Ретраим, а не сдаёмся.
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    logger.warning(
                        "captcha solver: goto %s failed (%d/3): %s",
                        url, attempt + 1, str(exc)[:120],
                    )
                    time.sleep(8)
            if last_err is not None:
                raise last_err

            solver._wait_for(
                pg,
                lambda: (
                    "Human Verification" in pg.content()
                    or "Choose all" in pg.content()
                    or len(pg.content()) > 25000
                ),
                timeout=25,
            )
            html = ""
            for _ in range(10):
                try:
                    html = pg.content()
                    break
                except Exception:
                    time.sleep(1)
            if "Human Verification" in html or "Choose all" in html:
                try:
                    ok, _n, self._round = solver.pass_captcha(pg, self._clip, self._round)
                    if not ok:
                        logger.warning("captcha not solved for %s", url)
                        return None, False
                except Exception:
                    pass  # гонка = страница уже уехала после успеха, проверяем куки
                time.sleep(3)
                cleared = True
            else:
                # челленджа не было — WAF пустил браузер сразу
                cleared = len(html) > 25000
            for _ in range(20):
                for c in ctx.cookies():
                    if c["name"] == "aws-waf-token":
                        token = c["value"]
                if token:
                    break
                time.sleep(1)
        except Exception as exc:
            logger.warning("token harvest failed for %s: %r", url, exc)
        finally:
            try:
                ctx.close()
            except Exception as exc:
                logger.warning("captcha solver: context close failed: %r", exc)
            try:
                br.close()
            except Exception as exc:
                logger.warning("captcha solver: browser NOT closed (leak risk): %r", exc)
        return token, cleared
