import { useEffect, useState } from "react";
import { Snackbar } from "../../components/Snackbar";
import { ApiError, claimProfileByAthleteId } from "../../lib/api";

/**
 * Сквозной путь «тизер на главной → вход → привязка» (гипотеза Т1).
 *
 * Раньше путь рвался: человек вводил ID в тизере, видел свои цифры, шёл
 * регистрироваться — и попадал в пустой кабинет, где всё надо было вводить
 * заново. По данным привязка происходит в первые десять минут или никогда
 * (420 из 674 за 120 дней — сразу, 195 — никогда), так что этот разрыв
 * приходился ровно на решающий момент.
 *
 * Теперь намерение переживает редирект на VK/Яндекс в localStorage, а сразу
 * после входа досылается на сервер. Именно localStorage, а не адрес: провайдер
 * возвращает человека на свой redirect_uri, наши query-параметры до него не
 * доезжают.
 */
const CLAIM_KEY = "sr_teaser_claim";

// Намерение живёт недолго: вернулся через час — это уже другой заход, и
// молча привязывать ему профиль по забытому вводу неправильно.
const CLAIM_TTL_MS = 60 * 60 * 1000;

type PendingClaim = {
  platform_code: string;
  athlete_id: string;
  platform_label: string;
  saved_at: number;
};

export function rememberTeaserClaim(
  platformCode: string,
  athleteId: string,
  platformLabel: string,
): void {
  try {
    const payload: PendingClaim = {
      platform_code: platformCode,
      athlete_id: athleteId,
      platform_label: platformLabel,
      saved_at: Date.now(),
    };
    localStorage.setItem(CLAIM_KEY, JSON.stringify(payload));
  } catch {
    // Приватный режим и переполненное хранилище — не повод ломать переход.
  }
}

/** Достаёт намерение и сразу его гасит: повторных попыток быть не должно. */
function takePendingClaim(): PendingClaim | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(CLAIM_KEY);
    localStorage.removeItem(CLAIM_KEY);
  } catch {
    return null;
  }
  if (raw === null) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as PendingClaim;
    if (
      typeof parsed?.platform_code !== "string" ||
      typeof parsed?.athlete_id !== "string" ||
      typeof parsed?.saved_at !== "number" ||
      Date.now() - parsed.saved_at > CLAIM_TTL_MS
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

type Outcome = { text: string; error: boolean } | null;

/**
 * Досылает отложенную привязку после входа и показывает, чем всё кончилось.
 *
 * Молча привязать было бы непонятно («откуда данные?»), а молча не привязать —
 * обидно: человек ждёт свою статистику. Поэтому любой исход виден, включая
 * отказ вроде «этот профиль уже привязан к другому аккаунту».
 */
export function TeaserClaimRunner({ userId }: { userId: string | null }) {
  const [outcome, setOutcome] = useState<Outcome>(null);

  useEffect(() => {
    if (userId === null) {
      return;
    }
    const pending = takePendingClaim();
    if (pending === null) {
      return;
    }
    let cancelled = false;
    claimProfileByAthleteId(pending.platform_code, pending.athlete_id)
      .then((result) => {
        if (cancelled) {
          return;
        }
        setOutcome(
          result.status === "already_linked"
            ? { text: `Профиль ${pending.platform_label} уже был привязан к вашему аккаунту.`, error: false }
            : {
                text: `Профиль ${pending.platform_label} привязан — считаем вашу статистику, она появится в кабинете.`,
                error: false,
              },
        );
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        const detail = err instanceof ApiError ? err.message : "";
        setOutcome({
          text: detail
            ? `Не получилось привязать профиль ${pending.platform_label}: ${detail}`
            : `Не получилось привязать профиль ${pending.platform_label} — сделайте это в кабинете.`,
          error: true,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  return (
    <Snackbar
      open={outcome !== null}
      title={outcome?.error ? "Привязка не прошла" : "Профиль привязан"}
      variant={outcome?.error ? "error" : "default"}
      onDismiss={() => setOutcome(null)}
    >
      {outcome?.text}
    </Snackbar>
  );
}
