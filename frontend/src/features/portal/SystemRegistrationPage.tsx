import { useState, type ReactNode } from "react";
import { SystemSignupLink } from "../../components/SystemSignupLink";
import { platformCodeLabel } from "../../lib/format";
import {
  S95_SIGNUP_SITES,
  isSignupPlatform,
  registrationRows,
  type RegistrationRowTone,
  type SignupPlatformCode,
} from "../../lib/systemSignup";
import {
  isAwaitedSignupPlatform,
  readSignupIntent,
  startAwaitingSignup,
  type SignupIntent,
  type SignupLinkMode,
} from "../../lib/signupIntent";
import type { User } from "../../lib/api";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { PortalSectionShell } from "./PortalSectionShell";
import { SignupLinkPanel } from "./SignupLinkPanel";
import "./systemRegistration.css";

/**
 * «Регистрация в беговых системах»: как получить штрихкод 5 вёрст, С95,
 * RunPark и parkrun и как потом привязать профиль у нас (25.10.2026).
 *
 * Сюда ведут ВСЕ призывы «Зарегистрироваться» на сайте, а уже отсюда человек
 * уходит на сайт системы (решения Дмитрия 25.10.2026). Шапка одна для всех;
 * на странице только четыре раскрывашки систем. Система из якоря адреса —
 * основная, первая и сразу раскрыта, остальные серые, привязанные — серые в
 * конце (registrationRows). Внутри раскрывашки — подробности, «Зарегистрироваться»
 * и «Уже есть аккаунт — привязать»; поле для номера появляется там же, под
 * кнопками (SignupLinkPanel). Откуда пришли, говорит ?from= — клик по ссылке
 * системы запишется как "page:<from>"; ?s95= — сайт С95 страны локации.
 *
 * Факты сверены с формами и правилами систем 23.10.2026. Тот же текст отдаёт
 * роботу пререндер (seo_service._REGISTRATION_GUIDES) — правьте вместе.
 */

// Откуда приходят на страницу. Чужое значение ?from= в сводку не пускаем.
const KNOWN_FROM = new Set(["map", "home", "location", "cabinet", "onboarding", "teaser", "search"]);

// Страница восстановления номера 5 вёрст — ссылка «Забыли свой ID?» у них в подвале.
const FIVE_VERST_REMINDER_URL = "https://5verst.ru/reminder/";

type Guide = {
  // Одна строка под названием — видна и в свёрнутом виде.
  summary: string;
  intro: string;
  needs: string;
  gets: string;
  link: string;
  tips: ReactNode[];
};

const GUIDES: Record<SignupPlatformCode, Guide> = {
  five_verst: {
    summary: "номер и QR-код — сразу после регистрации",
    intro: "Регистрация — на сайте 5verst.ru, один раз и навсегда.",
    needs: "ФИО, дата рождения, пол, почта, домашняя локация и пароль от личного кабинета 5 вёрст.",
    gets: "Номер участника вида A790… и QR-код — сразу на экране, копия придёт на почту. QR сканируют на финише: возьмите его с собой распечатанным или в телефоне.",
    link: "Сразу после регистрации — прямо здесь: под кнопкой «Зарегистрироваться» появится поле для номера. Уже есть номер — нажмите «Уже есть аккаунт — привязать».",
    tips: [
      "Почту iCloud 5 вёрст просит не указывать — письма туда не доходят.",
      <>
        Повторно регистрироваться не нужно. Потеряли номер?{" "}
        <a href={FIVE_VERST_REMINDER_URL} target="_blank" rel="noopener noreferrer">
          Восстановите его на сайте 5 вёрст ↗
        </a>
      </>,
    ],
  },
  s95: {
    summary: "подтвердите почту в течение 3 часов",
    intro: "Регистрация — на сайте С95. Бегать с профилем можно на любом забеге С95, в любой стране.",
    needs: "Имя, фамилия, почта, пол и домашний забег. Пароля нет — вход по ссылке из письма.",
    gets: "После подтверждения почты откроется профиль с персональным QR-кодом, штрихкод вида A770…",
    link: "Когда подтвердите почту — прямо здесь: под кнопкой «Зарегистрироваться» появится поле для ссылки на профиль (или штрихкода A770…). Уже есть профиль — нажмите «Уже есть аккаунт — привязать».",
    tips: [
      "Подтвердите почту в течение 3 часов — иначе С95 удалит регистрацию.",
      "Можно прийти и со штрихкодом 5 вёрст, RunPark или parkrun — результат засчитают в С95. Уже бегали так? Сначала найдите себя в кабинете по этому номеру: регистрация заведёт второй профиль, а объединяет их С95 только по просьбе через обратную связь.",
    ],
  },
  runpark: {
    summary: "можно со штрихкодом другой системы",
    intro: "Регистрация — на сайте runpark.ru, кнопка «Зарегистрироваться».",
    needs: "ФИО, дата рождения, город, почта, телефон и пол.",
    gets: "QR-код, который сканируют на финише.",
    link: "Привязать профиль RunPark на сайте можно только после первой пробежки (или волонтёрства) — тогда нажмите здесь «Уже есть аккаунт — привязать» и введите штрихкод.",
    tips: [
      "Можно бегать со штрихкодом другой системы: отметьте «Хочу использовать ИД другой системы» и укажите номер 5 вёрст, С95 или parkrun — его проверят при модерации.",
    ],
  },
  parkrun: {
    summary: "в России не проводится — для стартов за границей",
    intro: "В России parkrun не проводится с марта 2022 года — регистрация пригодится для старта за границей.",
    needs: "Анкета на английском на сайте parkrun.",
    gets: "Номер участника вида A… придёт письмом.",
    link: "Привязать профиль parkrun на сайте можно только после первого старта — тогда нажмите здесь «Уже есть аккаунт — привязать» и вставьте ссылку на профиль или штрихкод.",
    tips: [],
  },
};

function searchParam(name: string): string | null {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

function signupPlace(): string {
  const from = searchParam("from");
  return from && KNOWN_FROM.has(from) ? `page:${from}` : "page";
}

function targetFromHash(): SignupPlatformCode | null {
  const hash = window.location.hash.replace(/^#/, "");
  return isSignupPlatform(hash) ? hash : null;
}

function s95DomainFromQuery(): string | null {
  const domain = searchParam("s95");
  return domain && S95_SIGNUP_SITES.some((site) => site.domain === domain) ? domain : null;
}

// Что будет после нажатия. Ждать номер в этой вкладке можно только у 5 вёрст
// и С95: у RunPark и parkrun профиль до первого старта не привязать.
const ACTION_NOTES: Record<SignupPlatformCode, string> = {
  five_verst:
    "Сайт 5 вёрст откроется в новой вкладке, а здесь, под кнопками, появится поле для номера — привяжем профиль сразу после регистрации.",
  s95: "Сайт С95 откроется в новой вкладке, а здесь, под кнопками, появится поле для профиля — привяжем его, когда подтвердите почту.",
  runpark:
    "Сайт RunPark откроется в новой вкладке. Привязать профиль здесь можно будет только после первой пробежки.",
  parkrun:
    "Сайт parkrun откроется в новой вкладке. Привязать профиль здесь можно будет только после первого старта.",
};

/**
 * Кнопки внизу раскрытой строки: «Зарегистрироваться» и «Уже есть аккаунт —
 * привязать». Человек сначала читает, что понадобится и что делать потом, и
 * только затем уходит на сайт системы.
 */
function SignupActions({
  code,
  tone,
  place,
  s95Domain,
  onStartLink,
}: {
  code: SignupPlatformCode;
  tone: RegistrationRowTone;
  place: string;
  s95Domain: string | null;
  onStartLink: (code: SignupPlatformCode, mode: SignupLinkMode) => void;
}) {
  if (tone === "linked") {
    return <p className="sysreg-action-linked">Профиль {platformCodeLabel(code)} уже привязан к вашему кабинету.</p>;
  }
  // Сайт страны важен только домашнему забегу: в форме С95 он выбирается из
  // забегов страны сайта (events#search, in_country). Бегать можно везде.
  const otherS95 = S95_SIGNUP_SITES.filter((site) => site.domain !== (s95Domain ?? "s95.ru"));
  return (
    <div className="sysreg-action">
      <div className="sysreg-action-buttons">
        <SystemSignupLink
          code={code}
          place={place}
          s95Domain={code === "s95" ? s95Domain : null}
          className={`btn ${tone === "main" ? "primary" : "secondary"}`}
          onOpen={() => onStartLink(code, "after_signup")}
        >
          Зарегистрироваться в {platformCodeLabel(code)} ↗
        </SystemSignupLink>
        <button type="button" className="btn secondary" onClick={() => onStartLink(code, "existing")}>
          Уже есть аккаунт — привязать
        </button>
      </div>
      <p className="sysreg-action-note">
        {ACTION_NOTES[code]}
        {code === "s95" && (
          <>
            {" "}Кнопка ведёт на {s95Domain ?? "s95.ru"}: домашний забег в форме выбирается из
            забегов этой страны. Если он в другой — зарегистрируйтесь на её сайте:{" "}
            {otherS95.map((site, index) => (
              <span key={site.domain}>
                {index > 0 && ", "}
                <SystemSignupLink
                  code="s95"
                  place={place}
                  s95Domain={site.domain}
                  onOpen={() => onStartLink("s95", "after_signup")}
                >
                  {site.domain}
                </SystemSignupLink>
              </span>
            ))}
            .
          </>
        )}
      </p>
    </div>
  );
}

function GuideFacts({ code }: { code: SignupPlatformCode }) {
  const guide = GUIDES[code];
  return (
    <>
      <p className="sysreg-intro">{guide.intro}</p>
      <div className="sysreg-blocks">
        <section className="sysreg-block">
          <h3>Что понадобится</h3>
          <p>{guide.needs}</p>
        </section>
        <section className="sysreg-block">
          <h3>Что получите</h3>
          <p>{guide.gets}</p>
        </section>
        <section className="sysreg-block">
          <h3>Как привязать на run5k.run</h3>
          <p>{guide.link}</p>
        </section>
      </div>
      {guide.tips.length > 0 && (
        <section className="sysreg-important">
          <h3>Важно</h3>
          <ul>
            {guide.tips.map((tip, index) => (
              <li key={index}>{tip}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/**
 * Строка системы на всю ширину. Свёрнута — название и одна строка; раскрыта —
 * подробности, кнопки и, когда человек ушёл регистрироваться или нажал «Уже
 * есть аккаунт», поле для номера прямо под кнопками. Подробности в DOM всегда,
 * просто скрыты — их читают и поисковики.
 */
function SystemRow({
  code,
  tone,
  place,
  s95Domain,
  defaultOpen,
  intent,
  user,
  linkedHere,
  onStartLink,
  onCloseLink,
  onLinked,
}: {
  code: SignupPlatformCode;
  tone: RegistrationRowTone;
  place: string;
  s95Domain: string | null;
  defaultOpen: boolean;
  // Ждём номер именно этой системы (после регистрации или «уже есть аккаунт»).
  intent: SignupIntent | null;
  user: User | null;
  // Привязали только что, на этой странице.
  linkedHere: boolean;
  onStartLink: (code: SignupPlatformCode, mode: SignupLinkMode) => void;
  onCloseLink: () => void;
  onLinked: (code: SignupPlatformCode) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = `sysreg-${code}-info`;
  return (
    <section className={`card sysreg-row sysreg-row-${tone}${open ? " sysreg-row-open" : ""}`}>
      <h2 className="sysreg-row-title">
        <button
          type="button"
          className="sysreg-row-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((value) => !value)}
        >
          <span className={`portal-system-dot ${code}`} aria-hidden />
          <span className="sysreg-row-name">{platformCodeLabel(code)}</span>
          <span className="sysreg-row-summary">
            {tone === "linked" ? "профиль привязан ✓" : GUIDES[code].summary}
          </span>
          <span className="sysreg-row-more">{open ? "Свернуть" : "Подробнее"}</span>
          <span className="sysreg-row-chevron" aria-hidden>
            ▾
          </span>
        </button>
      </h2>
      <div id={bodyId} className="sysreg-row-body" hidden={!open}>
        <GuideFacts code={code} />
        {!linkedHere && (
          <SignupActions
            code={code}
            tone={tone}
            place={place}
            s95Domain={s95Domain}
            onStartLink={onStartLink}
          />
        )}
        {intent && (
          <SignupLinkPanel
            key={intent.mode}
            code={code}
            mode={intent.mode}
            user={user}
            initialInput={intent.input}
            startedAt={intent.started_at}
            active={open}
            onClose={onCloseLink}
            onLinked={onLinked}
          />
        )}
      </div>
    </section>
  );
}

export function SystemRegistrationPage() {
  const user = useOptionalUser();
  const [place] = useState(signupPlace);
  const [target] = useState(targetFromHash);
  const [s95Domain] = useState(s95DomainFromQuery);
  // Чей номер ждём (lib/signupIntent): пережил вход гостя через localStorage.
  const [intent, setIntent] = useState<SignupIntent | null>(readSignupIntent);
  // Привязали здесь же — строку не переставляем, а показываем в ней итог.
  const [justLinked, setJustLinked] = useState<string[]>([]);

  const linkedCodes = user ? user.linked_platform_codes ?? [] : null;
  const rows = registrationRows(target, linkedCodes);
  const allLinked = user != null && rows.every((row) => row.tone === "linked");

  const startLink = (code: SignupPlatformCode, mode: SignupLinkMode) => {
    if ((linkedCodes ?? []).includes(code)) {
      return;
    }
    // Новый профиль RunPark и parkrun до первого старта не привязать — после
    // «Зарегистрироваться» их поле не открываем; «Уже есть аккаунт» — можно.
    if (mode === "after_signup" && !isAwaitedSignupPlatform(code)) {
      return;
    }
    setIntent(startAwaitingSignup(code, mode));
  };
  // Раскрыта только система, ради которой пришли (якорь адреса). Без неё —
  // та, чей номер ждём после входа гостя. Остальные всегда свёрнуты.
  const goal = rows.find((row) => row.tone === "main" && row.code === target)?.code ?? null;

  return (
    <PortalSectionShell sidebar={{ active: "registration" }}>
      <div className="sysreg-page">
        <header className="sysreg-header">
          <h1>Регистрация в беговых системах</h1>
          <p className="sysreg-lead">
            Для результата в протоколе нужен личный штрихкод — его бесплатно дают при регистрации
            на сайте системы. run5k.run — неофициальный сайт статистики: регистрирует сама система,
            а здесь вы потом привяжете профиль.
          </p>
        </header>

        {/* Сессия ещё проверяется: не рисуем строки, чтобы через миг не
            перекрасить привязанные у вошедшего. */}
        {user !== undefined && (
          <>
            {allLinked && (
              <p className="sysreg-all-linked">
                Все системы уже привязаны к вашему профилю — регистрироваться больше негде.
              </p>
            )}
            <div className="sysreg-rows">
              {rows.map((row) => {
                const rowIntent =
                  intent?.platform_code === row.code &&
                  (row.tone !== "linked" || justLinked.includes(row.code))
                    ? intent
                    : null;
                return (
                  <SystemRow
                    key={row.code}
                    code={row.code}
                    tone={row.tone}
                    place={place}
                    s95Domain={s95Domain}
                    defaultOpen={goal ? row.code === goal : rowIntent != null}
                    intent={rowIntent}
                    user={user}
                    linkedHere={justLinked.includes(row.code)}
                    onStartLink={startLink}
                    onCloseLink={() => setIntent(null)}
                    onLinked={(code) => setJustLinked((prev) => [...prev, code])}
                  />
                );
              })}
            </div>
          </>
        )}
      </div>
    </PortalSectionShell>
  );
}
