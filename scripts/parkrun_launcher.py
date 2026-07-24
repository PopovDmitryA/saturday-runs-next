#!/usr/bin/env python3
"""Интерактивный запуск задач parkrun. `make parkrun` без параметров → это меню.

Спрашивает, что запускать и с какими параметрами, потом запускает.
Задачи:
  1) Обработать очередь сайта run5k.run  (стандартный демон, parkrun_mac.sh)
  2) Парсинг parkrun-профилей в диапазоне (мировой обход, macbook-воркер)
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
    no_browser = ask_yn("Без браузера (httpx, быстрее, но риск бана)?", default=False)
    if no_browser:
        env["NO_BROWSER"] = "1"
        env["FAST_DELAY"] = ask("Задержка между запросами, сек", "3")
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
    py = os.path.join(ROOT, ".conda-parkrun", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    print("\nЗапускаю macbook-воркер обхода…\n")
    os.execvp(py, [py, os.path.join(ROOT, "scripts", "mac_sweep_worker.py"),
                   "--delay", delay, "--limit", limit])


def main() -> None:
    print("═" * 56)
    print(" parkrun — что запускаем?")
    print("═" * 56)
    print("  1) Обработать очередь сайта run5k.run  (демон, браузер)")
    print("  2) Парсинг parkrun-профилей в диапазоне (обход, macbook)")
    choice = ask("Выбор", "1")
    if choice == "2":
        task_sweep()
    else:
        task_site()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nОтмена.")
