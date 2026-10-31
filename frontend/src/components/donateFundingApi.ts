// API донатов: месяц для блока «Поддержать проект» и админка /admin/donations.
// Отдельный модуль, а не lib/api.ts — тот файл одновременно правят соседние ветки.
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";

export type FundingCost = {
  title: string;
  hint: string | null;
  amount_rub: number;
};

export type FundingDonation = {
  received_on: string; // YYYY-MM-DD
  /** null — «Аноним»: просили не называть или перевод без имени. */
  name: string | null;
  amount_rub: number;
};

export type FundingMonth = {
  visible: boolean;
  month: string | null; // YYYY-MM
  current_month: string | null;
  first_month: string | null;
  /** Последний месяц со стрелкой «вперёд»: текущий или будущий с планом расходов. */
  last_month: string | null;
  costs: FundingCost[];
  cost_total_rub: number;
  donations: FundingDonation[];
  donated_total_rub: number;
};

export function getFundingMonth(month: string | null) {
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  return apiFetch<FundingMonth>(`/funding/month${query}`);
}

/**
 * Месяц блока со стрелками: null — текущий. Ответы помнятся, чтобы листание
 * туда-обратно не ходило на сервер второй раз. Ошибка — блок как без цифр.
 */
export function useFundingMonth() {
  const [month, setMonth] = useState<string | null>(null);
  // Повтор того же месяца после сбоя: setMonth с тем же значением эффект не
  // перезапускает, и стрелка молча переставала работать.
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<FundingMonth | null>(null);
  const [loading, setLoading] = useState(true);
  const cache = useRef(new Map<string, FundingMonth>());

  useEffect(() => {
    const key = month ?? "";
    const cached = cache.current.get(key);
    if (cached) {
      setData(cached);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getFundingMonth(month)
      .then((response) => {
        if (cancelled) return;
        cache.current.set(key, response);
        if (response.month) cache.current.set(response.month, response);
        setData(response);
      })
      .catch(() => {
        if (!cancelled && month === null) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [month, attempt]);

  const shift = useCallback(
    (delta: number) => {
      if (!data?.month) return;
      setMonth(shiftMonth(data.month, delta));
      setAttempt((value) => value + 1);
    },
    [data],
  );

  return { data, loading, shift };
}

// ---------- админка ----------

export type FundingItem = { id: string; title: string; hint: string | null };

export type FundingCostLine = {
  item_id: string;
  title: string;
  hint: string | null;
  amount_rub: number;
};

export type FundingAdminDonation = {
  id: string;
  received_on: string;
  amount_rub: number;
  donor_name: string | null;
  is_anonymous: boolean;
};

export type FundingAdminMonth = {
  month: string;
  current_month: string;
  max_month: string;
  show_public: boolean;
  items: FundingItem[];
  costs: FundingCostLine[];
  costs_copied_from: string | null;
  cost_total_rub: number;
  donations: FundingAdminDonation[];
  donated_total_rub: number;
  donor_names: { name: string; count: number }[];
};

export type FundingDonationPayload = {
  received_on: string;
  amount_rub: number;
  donor_name: string | null;
  is_anonymous: boolean;
};

export function getAdminFunding(month: string) {
  return apiFetch<FundingAdminMonth>(`/admin/funding?month=${encodeURIComponent(month)}`);
}

export function setFundingShowPublic(showPublic: boolean) {
  return apiFetch<{ show_public: boolean }>("/admin/funding/settings", {
    method: "PUT",
    body: JSON.stringify({ show_public: showPublic }),
  });
}

export function saveFundingCosts(month: string, items: { item_id: string; amount_rub: number }[]) {
  return apiFetch<FundingAdminMonth>(`/admin/funding/costs/${encodeURIComponent(month)}`, {
    method: "PUT",
    body: JSON.stringify({ items }),
  });
}

export function createFundingItem(title: string, hint: string | null) {
  return apiFetch<FundingItem>("/admin/funding/items", {
    method: "POST",
    body: JSON.stringify({ title, hint }),
  });
}

export function updateFundingItem(itemId: string, title: string, hint: string | null) {
  return apiFetch<FundingItem>(`/admin/funding/items/${itemId}`, {
    method: "PUT",
    body: JSON.stringify({ title, hint }),
  });
}

export function createFundingDonation(body: FundingDonationPayload) {
  return apiFetch<FundingAdminDonation>("/admin/funding/donations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateFundingDonation(donationId: string, body: FundingDonationPayload) {
  return apiFetch<FundingAdminDonation>(`/admin/funding/donations/${donationId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteFundingDonation(donationId: string) {
  return apiFetch<{ message: string }>(`/admin/funding/donations/${donationId}`, { method: "DELETE" });
}

// ---------- месяцы и суммы ----------

export function shiftMonth(month: string, delta: number): string {
  const [year, mon] = month.split("-").map(Number);
  const index = year * 12 + (mon - 1) + delta;
  const nextYear = Math.floor(index / 12);
  const nextMonth = (index % 12) + 1;
  return `${nextYear}-${String(nextMonth).padStart(2, "0")}`;
}

const MONTH_NAMES = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

// «Кто поддержал в октябре»
const MONTH_NAMES_PREPOSITIONAL = [
  "январе",
  "феврале",
  "марте",
  "апреле",
  "мае",
  "июне",
  "июле",
  "августе",
  "сентябре",
  "октябре",
  "ноябре",
  "декабре",
];

function monthIndex(month: string): number {
  return Number(month.split("-")[1]) - 1;
}

/** «Октябрь 2026» */
export function monthTitle(month: string): string {
  return `${MONTH_NAMES[monthIndex(month)]} ${month.split("-")[0]}`;
}

/** «Октябрь» */
export function monthName(month: string): string {
  return MONTH_NAMES[monthIndex(month)];
}

/** «октябре» */
export function monthPrepositional(month: string): string {
  return MONTH_NAMES_PREPOSITIONAL[monthIndex(month)];
}

export function formatRub(amount: number): string {
  return `${amount.toLocaleString("ru-RU")} ₽`;
}

/** «03.10» */
export function formatDayMonth(isoDate: string): string {
  const [, mon, day] = isoDate.split("-");
  return `${day}.${mon}`;
}

export function pluralTransfers(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} перевод`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} перевода`;
  return `${count} переводов`;
}
