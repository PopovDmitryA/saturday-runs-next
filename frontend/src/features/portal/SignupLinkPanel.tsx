import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  confirmFiveVerstProfile,
  confirmParkrunProfile,
  confirmRunparkProfile,
  confirmS95Profile,
  previewFiveVerstProfile,
  previewParkrunProfile,
  previewRunparkProfile,
  previewS95Profile,
  type ProfilePreview,
  type User,
} from "../../lib/api";
import { platformCodeLabel, pluralizeRu } from "../../lib/format";
import { loginHref } from "../../lib/loginReturn";
import { notifyProfileLinksChanged } from "../../lib/profileLinksEvents";
import { clearCachedUser } from "../../lib/useOptionalUser";
import {
  forgetSignupIntent,
  rememberSignupInput,
  signupInputToProfileInput,
  type SignupLinkMode,
} from "../../lib/signupIntent";
import type { SignupPlatformCode } from "../../lib/systemSignup";

/**
 * Поле «введите номер» внутри раскрывашки системы на /registration (решения
 * Дмитрия 25.10.2026). Два входа: после «Зарегистрироваться» — форма системы
 * открылась в новой вкладке, а эта ждёт новый номер; «Уже есть аккаунт —
 * привязать» — тот же путь для старого профиля. Дальше одинаково:
 * - гость вводит номер и уходит на вход — номер ждёт его в localStorage, и
 *   после входа привязка продолжается здесь же (lib/signupIntent);
 * - вошедший видит предпросмотр «Это вы?» и привязывает — способ привязки
 *   «регистрация через сайт» (link_method=signup), по нему и считаются
 *   приведённые регистрации;
 * - у кого система уже привязана, панели нет вовсе (решает страница).
 */

type Copy = { title: string; text: string };

// after_signup бывает только у 5 вёрст и С95 (решает страница), поэтому у
// RunPark и parkrun текста «ждём номер» нет.
const COPY: Record<SignupLinkMode, Partial<Record<SignupPlatformCode, Copy>>> = {
  after_signup: {
    five_verst: {
      title: "Ждём ваш номер 5 вёрст",
      text: "Регистрация открылась в соседней вкладке. Сразу после неё 5 вёрст покажет номер участника вида A790… — вставьте его сюда, и мы привяжем профиль к кабинету.",
    },
    s95: {
      title: "Ждём ваш профиль С95",
      text: "Регистрация открылась в соседней вкладке. Подтвердите почту по ссылке из письма С95 — откроется ваш профиль. Вставьте сюда его адрес или штрихкод A770…, и мы привяжем профиль к кабинету.",
    },
  },
  existing: {
    five_verst: {
      title: "Привязать профиль 5 вёрст",
      text: "Вставьте номер участника вида A790… или ссылку на профиль 5verst.ru/userstats/…",
    },
    s95: {
      title: "Привязать профиль С95",
      text: "Вставьте ссылку на профиль s95.ru/athletes/… или штрихкод вида A770…",
    },
    runpark: {
      title: "Привязать профиль RunPark",
      text: "Введите штрихкод, с которым бегаете в RunPark: свой вида A7000… или штрихкод другой системы. Найдём, если у профиля есть хотя бы одна пробежка или волонтёрство.",
    },
    parkrun: {
      title: "Привязать профиль parkrun",
      text: "Вставьте ссылку на профиль parkrun (…/parkrunner/123456) или штрихкод вида A123456. Если профиля ещё нет на сайте, его загрузка встанет в очередь — тогда попробуйте ещё раз позже.",
    },
  },
};

const INPUT_HINTS: Record<SignupPlatformCode, { placeholder: string; bad: string }> = {
  five_verst: {
    placeholder: "A790123456",
    bad: "Номер 5 вёрст выглядит так: A790123456. Можно вставить и ссылку на профиль 5verst.ru/userstats/…",
  },
  s95: {
    placeholder: "https://s95.ru/athletes/12345 или A770012345",
    bad: "Вставьте адрес профиля С95 (s95.ru/athletes/…) или штрихкод вида A770012345.",
  },
  runpark: {
    placeholder: "A7000123456",
    bad: "Штрихкод выглядит так: буква A и цифры, например A7000123456.",
  },
  parkrun: {
    placeholder: "https://www.parkrun.org.uk/parkrunner/123456/ или A123456",
    bad: "Вставьте ссылку на профиль parkrun (…/parkrunner/123456) или штрихкод вида A123456.",
  },
};

const PREVIEW: Record<SignupPlatformCode, (input: string, signal: AbortSignal) => Promise<ProfilePreview>> = {
  five_verst: previewFiveVerstProfile,
  s95: previewS95Profile,
  runpark: previewRunparkProfile,
  parkrun: previewParkrunProfile,
};

type Step =
  | { kind: "input" }
  | { kind: "login"; input: string }
  | { kind: "loading"; url: string }
  | { kind: "preview"; url: string; preview: ProfilePreview }
  | { kind: "linking"; url: string; preview: ProfilePreview }
  | { kind: "done"; name: string; hasHistory: boolean };

function countsLine(preview: ProfilePreview): string {
  const runs = preview.total_runs ?? 0;
  const volunteering = preview.total_volunteering ?? 0;
  if (runs === 0 && volunteering === 0) {
    return "пока без стартов — так и должно быть у свежего профиля";
  }
  const parts = [pluralizeRu(runs, ["пробежка", "пробежки", "пробежек"])];
  if (volunteering > 0) {
    parts.push(pluralizeRu(volunteering, ["волонтёрство", "волонтёрства", "волонтёрств"]));
  }
  return parts.join(" · ");
}

export function SignupLinkPanel({
  code,
  mode,
  user,
  initialInput,
  startedAt,
  active,
  onClose,
  onLinked,
}: {
  code: SignupPlatformCode;
  mode: SignupLinkMode;
  user: User | null;
  // Раскрыта ли строка: в свёрнутой предпросмотр сам не запускаем — это
  // живой запрос к системе, а человек его даже не увидит.
  active: boolean;
  // Номер, введённый гостем до входа: вошедшему сразу показываем «Это вы?».
  initialInput: string | null;
  // Когда нажали «Зарегистрироваться» — сервер сверит с активностью профиля.
  startedAt: number;
  onClose: () => void;
  onLinked: (code: SignupPlatformCode) => void;
}) {
  const copy = { ...(COPY[mode][code] ?? COPY.existing[code]!), ...INPUT_HINTS[code] };
  const label = platformCodeLabel(code);
  const [value, setValue] = useState(initialInput ?? "");
  const [step, setStep] = useState<Step>({ kind: "input" });
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const autoStarted = useRef(false);
  // Свой сигнал отмены — как у поиска в кабинете: живая загрузка профиля идёт
  // через очередь запросов к системе и бывает дольше общего тайм-аута apiFetch.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const lookUp = async (raw: string) => {
    const url = signupInputToProfileInput(code, raw);
    if (!url) {
      setError(copy.bad);
      return;
    }
    setError(null);
    if (!user) {
      rememberSignupInput(code, mode, raw.trim());
      setStep({ kind: "login", input: raw.trim() });
      return;
    }
    setStep({ kind: "loading", url });
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const preview = await PREVIEW[code](url, controller.signal);
      setStep({ kind: "preview", url, preview });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setStep({ kind: "input" });
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось найти профиль. Проверьте номер — или подождите пару минут, если регистрация только что прошла.",
      );
    }
  };

  // Гость ввёл номер, вошёл и вернулся сюда — продолжаем без повторного ввода.
  useEffect(() => {
    if (user && initialInput && active && !autoStarted.current) {
      autoStarted.current = true;
      void lookUp(initialInput);
    }
    // lookUp зависит от user и code — их и слушаем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, initialInput, active]);

  // Поле появляется прямо под нажатой кнопкой; докручиваем, только если оно
  // ушло за край экрана.
  useEffect(() => {
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void lookUp(value);
  };

  const link = async (url: string, preview: ProfilePreview) => {
    setStep({ kind: "linking", url, preview });
    setError(null);
    try {
      // «Регистрация через сайт» — только для номера, полученного по нашей
      // кнопке; старый профиль привязывается как обычная ссылка.
      const options = { signup: mode === "after_signup", signupStartedAt: startedAt };
      if (code === "five_verst") {
        await confirmFiveVerstProfile(url, options);
      } else if (code === "s95") {
        await confirmS95Profile(url, false, options);
      } else if (code === "runpark") {
        await confirmRunparkProfile(url);
      } else {
        await confirmParkrunProfile(url);
      }
      forgetSignupIntent();
      // Сессия в кэше и снимки страниц не знают о новой привязке — без сброса
      // кабинет и эта страница на миг показали бы систему непривязанной.
      clearCachedUser();
      notifyProfileLinksChanged();
      setStep({
        kind: "done",
        name: preview.display_name,
        hasHistory: (preview.total_runs ?? 0) + (preview.total_volunteering ?? 0) > 0,
      });
      onLinked(code);
    } catch (err) {
      // Предпросмотр на сервере живёт 15 минут: отвлёкся — перепрашиваем его
      // молча, вместо отсылки к несуществующей кнопке «Предпросмотр».
      if (err instanceof ApiError && err.status === 400) {
        void lookUp(url);
        return;
      }
      setStep({ kind: "preview", url, preview });
      setError(err instanceof Error ? err.message : "Не удалось привязать профиль");
    }
  };

  const close = () => {
    forgetSignupIntent();
    onClose();
  };

  return (
    <section ref={panelRef} className="sysreg-await" aria-live="polite">
      {step.kind === "done" ? (
        <>
          <h3 className="sysreg-await-title">Профиль {label} привязан ✓</h3>
          <p>
            {step.name} — теперь в вашем кабинете.{" "}
            {step.hasHistory
              ? "История стартов подтянется в кабинет в течение нескольких минут."
              : "Результаты появятся после первого старта."}
          </p>
          <div className="sysreg-await-actions">
            <a className="btn primary" href="/dashboard">
              Открыть кабинет
            </a>
          </div>
        </>
      ) : step.kind === "login" ? (
        <>
          <h3 className="sysreg-await-title">Номер запомнили — осталось войти</h3>
          <p>
            Чтобы привязать профиль {label} ({step.input}), войдите на run5k.run — через VK, Яндекс,
            Telegram или по коду на почту. После входа вы вернётесь сюда, и мы покажем профиль для
            подтверждения.
          </p>
          <div className="sysreg-await-actions">
            {/* Возврат — с якорем этой системы: после входа её строка будет
                первой и раскрытой, даже если пришли на страницу за другой. */}
            <a
              className="btn primary"
              href={loginHref(`${window.location.pathname}${window.location.search}#${code}`)}
            >
              Войти и привязать
            </a>
            <button type="button" className="btn btn-ghost" onClick={() => setStep({ kind: "input" })}>
              Изменить номер
            </button>
          </div>
        </>
      ) : step.kind === "preview" || step.kind === "linking" ? (
        <>
          <h3 className="sysreg-await-title">Это ваш профиль {label}?</h3>
          <div className="sysreg-await-preview">
            <b>{step.preview.display_name}</b>
            <span>{countsLine(step.preview)}</span>
          </div>
          {error && <p className="error-text">{error}</p>}
          <div className="sysreg-await-actions">
            <button
              type="button"
              className="btn primary"
              disabled={step.kind === "linking"}
              onClick={() => void link(step.url, step.preview)}
            >
              {step.kind === "linking" ? "Привязываем…" : "Да, привязать"}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={step.kind === "linking"}
              onClick={() => setStep({ kind: "input" })}
            >
              Нет, ввести другой номер
            </button>
          </div>
        </>
      ) : (
        <>
          <h3 className="sysreg-await-title">{copy.title}</h3>
          <p>{copy.text}</p>
          <form className="sysreg-await-form" onSubmit={submit}>
            <input
              className="input"
              type="text"
              inputMode="text"
              autoComplete="off"
              value={value}
              placeholder={copy.placeholder}
              aria-label={`Номер или ссылка на профиль ${label}`}
              disabled={step.kind === "loading"}
              onChange={(event) => setValue(event.target.value)}
            />
            <button type="submit" className="btn primary" disabled={step.kind === "loading" || !value.trim()}>
              {step.kind === "loading" ? "Ищем профиль…" : user ? "Найти профиль" : "Продолжить"}
            </button>
          </form>
          {step.kind === "loading" && (
            <p className="muted sysreg-await-hint">
              Загружаем профиль с сайта {label} — это может занять до минуты.
            </p>
          )}
          {error && <p className="error-text">{error}</p>}
        </>
      )}
      {step.kind !== "done" && (
        <button type="button" className="sysreg-await-close" onClick={close}>
          {mode === "after_signup" ? "Я не регистрировался(ась) — скрыть" : "Отмена"}
        </button>
      )}
    </section>
  );
}
