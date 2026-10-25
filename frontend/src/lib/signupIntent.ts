/**
 * «Ждём номер после регистрации» (решение Дмитрия 25.10.2026).
 *
 * Человек нажал на /registration «Зарегистрироваться в 5 вёрст» — форма
 * системы открылась в новой вкладке, а исходная вкладка ждёт номер, чтобы
 * сразу привязать профиль: вошедшему — предпросмотр и «Это вы?», гостю —
 * «войдите и привяжем». После регистрации ждём только там, где свежий профиль
 * можно сразу проверить и привязать: 5 вёрст и С95. RunPark и parkrun до
 * первого старта не привязать — у них поле открывает только «Уже есть
 * аккаунт — привязать».
 *
 * Намерение лежит в localStorage, а не в состоянии страницы: гость уходит на
 * вход (VK и Яндекс возвращают через свой адрес, ссылка бота открывается в
 * другой вкладке), и номер должен его дождаться. Живёт два часа.
 */
import { isSignupPlatform, type SignupPlatformCode } from "./systemSignup";

const INTENT_KEY = "sr_signup_link";
const INTENT_TTL_MS = 2 * 60 * 60 * 1000;

export type AwaitedSignupPlatform = "five_verst" | "s95";

// after_signup — нажали «Зарегистрироваться» и ждём новый номер (привязка
// запишется как «регистрация через сайт», link_method=signup); existing —
// «Уже есть аккаунт — привязать» (обычная привязка по ссылке: старый профиль
// не должен попадать в счёт приведённых регистраций).
export type SignupLinkMode = "after_signup" | "existing";

export type SignupIntent = {
  platform_code: SignupPlatformCode;
  mode: SignupLinkMode;
  // Номер или ссылка, которые гость ввёл до входа; пусто — ещё не вводил.
  input: string | null;
  saved_at: number;
  // Когда нажали «Зарегистрироваться» (after_signup): сервер не засчитает
  // регистрацией через сайт профиль с активностью до этого дня.
  started_at: number;
};

export function isAwaitedSignupPlatform(code: string): code is AwaitedSignupPlatform {
  return code === "five_verst" || code === "s95";
}

function write(intent: SignupIntent): void {
  try {
    localStorage.setItem(INTENT_KEY, JSON.stringify(intent));
  } catch {
    // Приватный режим — форма всё равно покажется до перезагрузки вкладки.
  }
}

/** Ушёл регистрироваться в систему или открыл «уже есть аккаунт»: ждём номер. */
export function startAwaitingSignup(code: SignupPlatformCode, mode: SignupLinkMode): SignupIntent {
  const current = readSignupIntent();
  const now = Date.now();
  const intent: SignupIntent = {
    platform_code: code,
    mode,
    input: current?.platform_code === code ? current.input : null,
    saved_at: now,
    started_at: now,
  };
  write(intent);
  return intent;
}

/** Гость ввёл номер до входа — после входа привязка продолжится с ним. */
export function rememberSignupInput(code: SignupPlatformCode, mode: SignupLinkMode, input: string): void {
  const current = readSignupIntent();
  const now = Date.now();
  write({
    platform_code: code,
    mode,
    input,
    saved_at: now,
    started_at: current?.platform_code === code ? current.started_at : now,
  });
}

export function readSignupIntent(): SignupIntent | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(INTENT_KEY);
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as SignupIntent;
    const code = String(parsed?.platform_code);
    const mode: SignupLinkMode = parsed.mode === "existing" ? "existing" : "after_signup";
    if (
      !isSignupPlatform(code) ||
      // После регистрации номер ждут только 5 вёрст и С95.
      (mode === "after_signup" && !isAwaitedSignupPlatform(code)) ||
      typeof parsed.saved_at !== "number" ||
      Date.now() - parsed.saved_at > INTENT_TTL_MS
    ) {
      forgetSignupIntent();
      return null;
    }
    return {
      platform_code: code,
      mode,
      input: typeof parsed.input === "string" ? parsed.input : null,
      saved_at: parsed.saved_at,
      started_at: typeof parsed.started_at === "number" ? parsed.started_at : parsed.saved_at,
    };
  } catch {
    forgetSignupIntent();
    return null;
  }
}

export function forgetSignupIntent(): void {
  try {
    localStorage.removeItem(INTENT_KEY);
  } catch {
    // ignore
  }
}

const S95_DOMAIN_RE = /\bs95\.(ru|by|rs|kz)\b/i;

/**
 * Номер или ссылка из формы → то, что принимает предпросмотр системы. null —
 * ввод не похож на номер этой системы.
 *
 * 5 вёрст: «A790123456», «790123456» или ссылка 5verst.ru/userstats/….
 * С95: ссылка s95.ru/athletes/… (и сайтов других стран), номер участника из
 * неё или штрихкод A770… (С95 печатает 770000000 + номер участника тем, у
 * кого нет кода другой системы).
 * RunPark: штрихкод — свой A7000… или любой другой системы, с которым человек
 * бегает; ищется по нашей базе и базе RunPark.
 * parkrun: ссылка на профиль (…/parkrunner/123456) или штрихкод A123456.
 */
export function signupInputToProfileInput(code: SignupPlatformCode, raw: string): string | null {
  const value = raw.trim().replace(/\s+/g, "");
  if (!value) {
    return null;
  }
  if (code === "five_verst") {
    const fromUrl = /5verst\.ru\/userstats\/(\d{5,12})/i.exec(value);
    const digits = fromUrl?.[1] ?? /^[AАaа]?(\d{5,12})$/.exec(value)?.[1];
    return digits ? `https://5verst.ru/userstats/${digits}/` : null;
  }
  if (code === "runpark") {
    const digits = /^[AАaа]?(\d{4,12})$/.exec(value)?.[1];
    return digits ? `A${digits}` : null;
  }
  if (code === "parkrun") {
    // Номер участника parkrun один на все страны: из ссылки любой страны
    // (parkrun.pl, …/bushy/parkrunner/…) берём его и шлём штрихкодом —
    // предпросмотр по ссылке понимает только parkrun.org.uk.
    const fromUrl = /parkrun\.[a-z.]+\/(?:[^/?#]+\/)?parkrunner\/(\d{1,9})/i.exec(value);
    const digits = fromUrl?.[1] ?? /^[AАaа]?(\d{1,9})$/.exec(value)?.[1];
    return digits ? `A${digits}` : null;
  }
  const fromUrl = /athletes\/(\d{1,9})/i.exec(value);
  if (fromUrl && S95_DOMAIN_RE.test(value)) {
    const domain = S95_DOMAIN_RE.exec(value)?.[0].toLowerCase() ?? "s95.ru";
    return `https://${domain}/athletes/${fromUrl[1]}`;
  }
  // У половины кодов С95 в базе нет буквы — принимаем и так.
  const barcode = /^[AАaа]?(\d{9})$/.exec(value);
  if (barcode) {
    const number = Number(barcode[1]);
    return number > 770_000_000 && number < 790_000_000
      ? `https://s95.ru/athletes/${number - 770_000_000}`
      : null;
  }
  const athleteId = /^(\d{1,8})$/.exec(value);
  return athleteId ? `https://s95.ru/athletes/${athleteId[1]}` : null;
}
