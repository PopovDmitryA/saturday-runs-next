// События шаринга для админ-«Популярности».
//
// Пишутся в тот же канал, что АБ-события главной (POST /api/stats/event →
// ab_events), но с experiment="share" — выборки шаринга не смешиваются с
// экспериментом home_v1. Бэкенд знает канал в KNOWN_EXPERIMENTS (ab_service).
//
// Воронка: share_moment_shown → share_open → share_success. Значения value
// кодируются префиксами (как home_link_click): "сюжет:вход" и "канал:сюжет".

import { abVisitorKey } from "../../lib/abTest";
import type { ShareEntryPoint, ShareSubjectKind } from "./types";

export const SHARE_EXPERIMENT = "share";

function sendShareEvent(eventType: string, value: string): void {
  const body = JSON.stringify({
    experiment: SHARE_EXPERIMENT,
    // Канал вне АБ-теста: вариант не осмыслен, но поле обязательное.
    variant: "-",
    visitor_key: abVisitorKey(),
    event_type: eventType,
    value: value.slice(0, 128),
    path: window.location.pathname,
  });
  try {
    navigator.sendBeacon("/api/stats/event", new Blob([body], { type: "application/json" }));
  } catch {
    // ignore analytics errors
  }
}

const shownOnce = new Set<string>();

/** Показ области-приглашения — один раз на страницу за просмотр. */
export function trackShareMomentShown(subject: ShareSubjectKind, entry: ShareEntryPoint): void {
  const key = `${subject}:${entry}:${window.location.pathname}`;
  if (shownOnce.has(key)) {
    return;
  }
  shownOnce.add(key);
  sendShareEvent("share_moment_shown", `${subject}:${entry}`);
}

/** Открытие шторки. */
export function trackShareOpen(subject: ShareSubjectKind, entry: ShareEntryPoint): void {
  sendShareEvent("share_open", `${subject}:${entry}`);
}

/** Свайп лука или смена формата: value = "look:индиго" | "format:wide". */
export function trackShareTemplateSwitch(kind: "look" | "format", id: string): void {
  sendShareEvent("share_template_switch", `${kind}:${id}`);
}

/** Открытие настройки и её действия: metrics / photo / tone. */
export function trackShareCustomize(what: string): void {
  sendShareEvent("share_customize", what);
}

/** Доведённый до конца шеринг: канал system | download | copy. */
export function trackShareSuccess(channel: string, subject: ShareSubjectKind): void {
  sendShareEvent("share_success", `${channel}:${subject}`);
}
