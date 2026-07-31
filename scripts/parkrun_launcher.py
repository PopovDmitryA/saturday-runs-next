#!/usr/bin/env python3
"""Интерактивный запуск задач parkrun. `make parkrun` без параметров → это меню.

Спрашивает, что запускать и с какими параметрами, потом запускает.
Задачи:
  1) Обработать очередь сайта run5k.run  (стандартный демон, parkrun_mac.sh)
  2) Парсинг parkrun-профилей в диапазоне (мировой обход, macbook-воркер)
  3) Обработать собранное сырьё в БД     (офлайн-парсер, parkrun-monitoring)
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    v = input(f"  {prompt}{suffix}: ").strip()
    return v or default


def ask_yn(prompt: str, default: bool = False) -> bool:
    d = "Д/н" if default else "д/Н"
    v = input(f"  {prompt} [{d}]: ").strip().lower()
    if not v:
        return default
    return v in ("y", "yes", "д", "да")


def task_site() -> None:
    print("\n— Очередь сайта run5k.run —")
    env = dict(os.environ)
    fast = ask_yn("Быстрый режим (httpx + решатель капчи CLIP)?", default=False)
    if fast:
        env["NO_BROWSER"] = "1"
        env["SOLVE_CAPTCHA"] = "1"
        env["FAST_DELAY"] = ask("Задержка между запросами, сек", "3")
        print("  httpx качает профили; при капче браузер решит её сам (CLIP).")
    limit = ask("Лимит профилей за прогон (пусто = штатно)", "")
    if limit:
        env["LIMIT"] = limit
    if ask_yn("Тихий режим (меньше шума в терминале)?", default=True):
        env["QUIET"] = "1"
    if ask_yn("Только очередь, без плановых синков?", default=False):
        env["PENDING_ONLY"] = "1"
    print("\nЗапускаю демон сайта…\n")
    os.execvpe("bash", ["bash", os.path.join(ROOT, "scripts", "parkrun_mac.sh")], env)


def task_sweep() -> None:
    print("\n— Мировой обход parkrun (macbook) —")
    delay = ask("Задержка между атлетами, сек", "12")
    limit = ask("Сколько атлетов за сессию (0 = без предела)", "0")
    browser = ask_yn("Ночной режим — сразу через браузер (капча почти не выпадает)?",
                     default=False)
    py = os.path.join(ROOT, ".conda-parkrun", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    argv = [py, os.path.join(ROOT, "scripts", "mac_sweep_worker.py"),
            "--delay", delay, "--limit", limit]
    if browser:
        argv.append("--browser")
    print("\nЗапускаю macbook-воркер обхода…\n")
    os.execvp(py, argv)


def task_parse() -> None:
    print("\n— Офлайн-парсер собранного сырья (в БД) —")
    print("  Читает файлы, которые free-сборщик сложил на сервере (data/raw),")
    print("  парсит и пишет в pm-postgres. Можно запускать параллельно с виндой.")
    pm = os.path.join(os.path.expanduser("~"), "Projects", "parkrun-monitoring")
    script = os.path.join(pm, "athlete_sweep", "file_parser.py")
    if not os.path.exists(script):
        sys.exit(f"не нашёл парсер: {script}\nклонируй parkrun-monitoring в ~/Projects/")
    print("  Потоки: пока один поток ждёт файл с сервера, остальные работают.")
    print("  Замер на Маке: 1 поток ≈ 13/мин, 8 потоков ≈ 119/мин. Выше 8 роста почти нет.")
    threads = ask("Во сколько потоков обрабатывать", "8")
    limit = ask("Сколько атлетов за сессию (0 = без предела)", "0")
    delete = ask_yn("Удалять файлы сырья после записи в БД?", default=False)
    py = os.path.join(ROOT, ".conda-parkrun", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    argv = [py, script, "--limit", limit, "--threads", threads]
    if delete:
        argv.append("--delete")
    print("\nЗапускаю офлайн-парсер…\n")
    os.execvp(py, argv)


def task_waf() -> None:
    print("\n— Обход через приватный выход с решателем капчи —")
    print("  Страницы качает httpx (быстро), браузер поднимается только когда")
    print("  прилетела капча: решает её сам (CLIP) и отдаёт токен. ~1 капча на 25 атлетов.")
    pm = os.path.join(os.path.expanduser("~"), "Projects", "parkrun-monitoring")
    if not os.path.exists(os.path.join(pm, "athlete_sweep", "waf_solver.py")):
        sys.exit(f"не нашёл решатель в {pm}\nклонируй parkrun-monitoring в ~/Projects/")
    print("\n  Выходы (порт на сервере): de2=10859, lt=10853, us=10855, ee=10875,")
    print("                            gf05=10864, gf06=10863, gf07=10862")
    port = ask("Порт выхода", "10859")
    limit = ask("Сколько атлетов за сессию (0 = без предела, до Ctrl+C)", "0")
    delay = ask("Задержка между атлетами, сек", "2")
    py = os.path.join(ROOT, ".conda-parkrun", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    print("\nЗапускаю обход с решателем капчи…\n")
    os.execvpe(py, [py, "-u", "-m", "athlete_sweep.waf_solver", "unused",
                    "--fast", "--exit-port", port, "--limit", limit, "--delay", delay],
               {**os.environ, "PYTHONPATH": pm})


def task_event_pages() -> None:
    print("\n— Описания событий: главная + /course/ каждой локации —")
    print("  ~2950 активных событий × 2 страницы, свой канал (httpx),")
    print("  капчи решает сам (CLIP). Токен WAF свой на каждый домен страны.")
    print("  Уже собранное пропускается — можно прерывать и дозапускать.")
    pm = os.path.join(os.path.expanduser("~"), "Projects", "parkrun-monitoring")
    if not os.path.exists(os.path.join(pm, "athlete_sweep", "event_pages.py")):
        sys.exit(f"не нашёл сборщик в {pm}\nобнови parkrun-monitoring (git pull)")
    delay = ask("Задержка между страницами, сек", "2")
    limit = ask("Сколько событий за сессию (0 = все)", "0")
    py = os.path.join(ROOT, ".conda-parkrun", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    print("\nЗапускаю сбор описаний…\n")
    os.execvpe(py, [py, "-u", "-m", "athlete_sweep.event_pages",
                    "--delay", delay, "--limit", limit],
               {**os.environ, "PYTHONPATH": pm})


def main() -> None:
    print("═" * 56)
    print(" parkrun — что запускаем?")
    print("═" * 56)
    print("  1) Обработать очередь сайта run5k.run  (демон, браузер)")
    print("  2) Парсинг parkrun-профилей в диапазоне (обход, macbook)")
    print("  3) Обработать собранное сырьё в БД      (офлайн-парсер)")
    print("  4) Обход через выход + решатель капчи   (httpx + CLIP)")
    print("  5) Описания событий: главная + трасса   (httpx + CLIP)")
    choice = ask("Выбор", "1")
    if choice == "2":
        task_sweep()
    elif choice == "3":
        task_parse()
    elif choice == "4":
        task_waf()
    elif choice == "5":
        task_event_pages()
    else:
        task_site()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nОтмена.")
