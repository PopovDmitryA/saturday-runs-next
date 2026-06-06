from __future__ import annotations

from typing import Any


def format_admin_stats(payload: dict[str, Any]) -> str:
    overview = payload.get("overview") or {}
    period_days = payload.get("period_days", 30)
    links = overview.get("links_by_platform") or {}

    lines = [
        f"📊 Статистика ЛК за {period_days} дн.",
        "",
        f"Пользователи: {overview.get('users_total', 0)}",
        f"  с согласием: {overview.get('users_with_consent', 0)}",
        f"  активны за период: {overview.get('users_active_period', 0)}",
        f"  с привязками: {overview.get('users_with_any_link', 0)}",
        f"  все 3 платформы: {overview.get('users_with_all_three_links', 0)}",
        "",
        f"Привязки: {overview.get('platform_links_total', 0)}",
        f"  5 вёрst: {links.get('five_verst', 0)}",
        f"  с95: {links.get('s95', 0)}",
        f"  parkrun: {links.get('parkrun', 0)}",
        "",
        f"Участники: {overview.get('participants_total', 0)}",
        f"События: {overview.get('events_total', 0)}",
        f"Результаты: {overview.get('run_results_total', 0)}",
        f"Локации: {overview.get('locations_total', 0)}",
        "",
        f"Sync jobs: {overview.get('sync_jobs_active', 0)} активных / {overview.get('sync_jobs_total', 0)} всего",
        f"Запросов входа за период: {overview.get('login_requests_period', 0)}",
    ]

    pageviews = payload.get("pageviews_by_day") or []
    if pageviews:
        total_views = sum(int(day.get("total", 0)) for day in pageviews)
        total_unique = sum(int(day.get("unique_visitors", 0)) for day in pageviews)
        lines.extend(["", f"Просмотры страниц: {total_views} (уник. ~{total_unique})"])

    return "\n".join(lines)
