import { useEffect, useState, type ReactNode } from "react";
import { useCachedResource } from "../../hooks/useCachedResource";
import { ParticipationBadge } from "../../components/ParticipationBadge";
import { PlatformBadge } from "../../components/PlatformBadge";
import { RateRunModal } from "../../components/RateRunModal";
import { Snackbar } from "../../components/Snackbar";
import { useSnackbar } from "../../hooks/useSnackbar";
import {
  ApiError,
  getLocationPage,
  getLocationRateStatus,
  type EligibleRun,
  type LocationPage,
  type LocationRateStatus,
} from "../../lib/api";
import { formatDateLong, pluralizeRu } from "../../lib/format";
import { locationHintFor, rememberLocationHint } from "../../lib/locationHint";
import { LOGIN_NEXT_PARAM, loginHref } from "../../lib/loginReturn";
import { flushMetrikaHit } from "../../lib/metrika";
import { applyPageMeta, locationPageMeta } from "../../lib/pageMeta";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { RateLinkShare, locationRatePath } from "./RateLinkShare";
import "./locationRate.css";

/**
 * Вкладка «Оценка» локации: /locations/<slug>/rate (карточка бэклога
 * 23.09.2026 «Кнопка-ссылка "Оценить локацию"», решение Дмитрия 22.10.2026).
 *
 * Организатор или любой участник кидает ссылку на неё в чат локации, и каждый,
 * кто откроет, видит свой последний старт здесь и кнопку «Оценить» — или
 * понятную причину, почему оценить нельзя. До 22.10.2026 то же делало окно
 * по ?rate=1 поверх страницы локации, а копировать ссылку можно было
 * таблеткой, видной не всем: механика была скрытой. Вкладка — обычная
 * страница раздела, её видно в навигации, а ссылка на неё — её адрес.
 *
 * Окно оценки открывается только кнопкой: страница показывает состояние и
 * ничего не открывает сама. Можно ли оценить и почему нет, решает сервер
 * (GET /ratings/location/<slug> поверх тех же правил, что у карточки
 * «оцените свой старт» на обзоре локации).
 */

const DAY_FORMS = ["день", "дня", "дней"] as const;
const PROFILES_HREF = "/dashboard#profiles";
// Протокол субботы грузится в течение дня: свежий старт мог ещё не доехать
// до истории, и человек из чата видит прошлый старт или «вас здесь не было».
const FRESH_START_HINT =
  "Были на последнем старте? Загляните сюда позже — он появится, когда загрузится протокол.";
// Сколько дней после субботы её протокол ещё может ехать: 5 вёрст и S95
// выкладывают его в субботу, поздние — в воскресенье и понедельник.
const FRESH_PROTOCOL_GRACE_DAYS = 2;

function isoLocalDate(date: Date): string {
  const yyyy = String(date.getFullYear()).padStart(4, "0");
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

/**
 * Протокол последней субботы локации может ещё не быть загружен: с субботы
 * прошло не больше FRESH_PROTOCOL_GRACE_DAYS, а последний загруженный старт
 * локации — раньше неё. Только тогда «загляните позже» — правда (ревью
 * 22.10.2026): со вторника протокол давно на месте, и не нашёлся человек
 * из-за привязки или другой системы, а не из-за протокола.
 */
function freshProtocolPending(page: LocationPage, now: Date = new Date()): boolean {
  if (page.is_paused || page.is_cancelled) {
    return false;
  }
  const saturday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  // getDay: вс = 0 … сб = 6; дней с последней субботы: сб → 0, вс → 1, пн → 2.
  const sinceSaturday = (saturday.getDay() + 1) % 7;
  if (sinceSaturday > FRESH_PROTOCOL_GRACE_DAYS) {
    return false;
  }
  saturday.setDate(saturday.getDate() - sinceSaturday);
  const lastEvent = page.stats.last_event_date;
  return !lastEvent || lastEvent < isoLocalDate(saturday);
}

type Mine =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "error" }
  | { kind: "ready"; status: LocationRateStatus };

function RunSummary({ run }: { run: EligibleRun }) {
  const details: string[] = [];
  if (run.participation_type === "volunteer") {
    if (run.volunteer_role) details.push(run.volunteer_role);
  } else {
    if (run.finish_time_display) details.push(run.finish_time_display);
    if (run.position != null) details.push(`${run.position} место`);
  }
  const rating = run.my_rating;
  const score = rating?.score_overall ?? 0;
  return (
    <div className="rate-tab-run">
      <p className="rate-tab-run-line">
        <span className="rate-tab-run-date">{formatDateLong(run.event_date)}</span>
        <PlatformBadge code={run.platform_code} />
        <ParticipationBadge type={run.participation_type} />
        {details.length > 0 && <span className="muted">{details.join(" · ")}</span>}
      </p>
      {rating && score > 0 && (
        <p className="rate-tab-run-line">
          {/* role="img": без роли дикторы пропускают aria-label у span и читают
              «звезда» на каждый символ, серые тоже. */}
          <span className="rate-tab-stars" role="img" aria-label={`Ваша оценка: ${score} из 5`}>
            {"★".repeat(score)}
            <span className="rate-tab-stars-empty">{"★".repeat(5 - score)}</span>
          </span>
          <span className="muted">оценка от {formatDateLong(rating.updated_at)}</span>
        </p>
      )}
    </div>
  );
}

function LocationRateContent({ slug }: { slug: string }) {
  // Ответ страницы локации общий с обзором (тот же ключ кэша): с обзора сюда
  // приходят без ожидания. Отсюда — имя и основной слаг для ссылки.
  const { data: page, notFound, error } = useCachedResource(
    `locations:page:${slug}`,
    () => getLocationPage(slug),
    [slug],
    {
      errorText: "Не удалось загрузить локацию",
      onLoaded: (payload) => {
        rememberLocationHint({ slug: payload.slug, name: payload.name });
        applyPageMeta(locationPageMeta(payload, { rate: true }));
        // Адрес — по основному слагу, как у обзора: его скопируют из строки
        // браузера и пришлют в чат.
        if (payload.slug && payload.slug !== slug) {
          window.history.replaceState(null, "", locationRatePath(payload.slug));
        }
      },
      // Просмотр был, пусть и неудачный — досылаем с родовым заголовком.
      onSettled: flushMetrikaHit,
    },
  );
  // Своё состояние не кэшируем: оно личное и меняется после оценки.
  const [mine, setMine] = useState<Mine>({ kind: "loading" });
  // Новый запрос: «Попробовать снова», «Проверить снова» и после оценки.
  const [attempt, setAttempt] = useState(0);
  const [modalRun, setModalRun] = useState<EligibleRun | null>(null);
  const { snackbar, showSnackbar, dismissSnackbar } = useSnackbar();

  useEffect(() => {
    let cancelled = false;
    // Гость или нет — решает ответ сервера (401), а не кэш сессии: «гость»,
    // оставшийся от захода до входа, мигнул бы призывом войти перед уже
    // вошедшим, а сбой /auth/me отправил бы его на вход.
    getLocationRateStatus(slug)
      .then((status) => {
        if (!cancelled) setMine({ kind: "ready", status });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setMine(err instanceof ApiError && err.status === 401 ? { kind: "guest" } : { kind: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [slug, attempt]);

  const retry = () => {
    setMine({ kind: "loading" });
    setAttempt((value) => value + 1);
  };
  // После оценки прежнее состояние стоит на экране, пока не приедет новое:
  // без мигания «Ищем ваш старт…».
  const refresh = () => setAttempt((value) => value + 1);

  const sidebarLocation = page ? { slug: page.slug, name: page.name } : locationHintFor(slug);

  if (notFound) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card">
          <p className="muted">Локация не найдена.</p>
          <p>
            <a href="/locations">Все локации</a>
          </p>
        </div>
      </PortalSectionShell>
    );
  }
  if (error) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <div className="card error">
          <p>{error}</p>
        </div>
      </PortalSectionShell>
    );
  }
  if (!page) {
    return (
      <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
        <p className="muted">Загрузка…</p>
      </PortalSectionShell>
    );
  }

  const ratePath = locationRatePath(page.slug);
  const retryButton = (label: string) => (
    <button type="button" className="btn primary" onClick={retry}>
      {label}
    </button>
  );
  const openOwn = (run: EligibleRun | null) =>
    run?.my_rating ? (
      <button type="button" className="btn primary" onClick={() => setModalRun(run)}>
        {run.my_rating.editable ? "Изменить оценку" : "Посмотреть оценку"}
      </button>
    ) : null;

  let body: ReactNode;
  let actions: ReactNode = null;

  if (mine.kind === "loading") {
    body = (
      <p className="muted" role="status">
        Ищем ваш старт…
      </p>
    );
  } else if (mine.kind === "guest") {
    body = (
      <p className="rate-tab-lead">
        Войдите — найдём ваш последний старт здесь, пробежку или волонтёрство, и предложим его
        оценить.
      </p>
    );
    // Адрес возврата — основной адрес вкладки, а не текущий: его могли открыть
    // по второстепенному слагу.
    actions = (
      <a className="btn primary" href={loginHref(ratePath)}>
        Войти и оценить
      </a>
    );
  } else if (mine.kind === "error") {
    body = <p className="rate-tab-lead">Не получилось проверить, можно ли оценить старт.</p>;
    actions = retryButton("Попробовать снова");
  } else {
    const status = mine.status;
    const run = status.run;
    const windowDays = pluralizeRu(status.window_days, DAY_FORMS);
    const lastVisit = status.last_visit_date ? formatDateLong(status.last_visit_date) : null;
    const protocolPending = freshProtocolPending(page);
    const freshHint = protocolPending ? <p className="rate-tab-hint">{FRESH_START_HINT}</p> : null;
    // Новичку без единой привязки — онбординг: он умеет вернуть на страницу
    // (?next=), и человек из чата после привязки снова здесь (ревью
    // 22.10.2026). Кабинет так не умеет, а «Добро пожаловать» тому, у кого
    // привязки давно есть, не к месту — им подсказываем вернуться самим.
    const profilesLink = (primary: boolean, href: string = PROFILES_HREF) => (
      <a className={`btn ${primary ? "primary" : "secondary"}`} href={href}>
        Привязать профиль
      </a>
    );

    switch (status.status) {
      case "can_rate":
        if (run) {
          // Свежий старт уже оценён, а сервер предлагает более ранний из окна
          // (ревью 22.10.2026): последний визит позже этого старта. У добора
          // истории (is_legacy) так не бывает — его дают, только пока здесь нет
          // ни одной оценки, а более поздний визит там — давний parkrun.
          const newerRated =
            !run.is_legacy &&
            status.last_visit_date !== null &&
            status.last_visit_date > run.event_date;
          body = (
            <>
              <p className="rate-tab-lead">
                {newerRated
                  ? `Последний старт здесь, ${lastVisit}, вы уже оценили — спасибо! Можно оценить и более ранний:`
                  : "Ваш последний старт здесь, который можно оценить:"}
              </p>
              <RunSummary run={run} />
              {!status.run_is_recent && !newerRated && (
                <p className="rate-tab-note">
                  {run.is_legacy
                    ? "Это давний старт: из прошлых можно оценить один на локацию — там, где вы ещё не оценивали."
                    : "Это старт не с последней недели."}
                </p>
              )}
              {!status.run_is_recent && freshHint}
            </>
          );
          actions = (
            <button type="button" className="btn primary" onClick={() => setModalRun(run)}>
              ⭐ Оценить
            </button>
          );
          break;
        }
        // can_rate без старта сервер не отдаёт; на всякий случай — как «не нашли».
        body = <p className="rate-tab-lead">Мы не нашли старт, который можно оценить.</p>;
        actions = retryButton("Проверить снова");
        break;
      case "rated":
        body = (
          <>
            <p className="rate-tab-lead">Ваш последний старт здесь уже оценён — спасибо!</p>
            {run && <RunSummary run={run} />}
            {run?.my_rating && !run.my_rating.editable && (
              <p className="rate-tab-note">Оценка зафиксирована — исправить её уже нельзя.</p>
            )}
            {!status.run_is_recent && freshHint}
          </>
        );
        actions = openOwn(run);
        break;
      case "too_old":
        body = (
          <>
            <p className="rate-tab-lead">
              {lastVisit ? `Последний раз вы были здесь ${lastVisit}. ` : ""}
              Новую оценку можно поставить старту за последние {windowDays}.
            </p>
            {run && (
              <>
                <p className="rate-tab-note">
                  Давние старты можно оценить только там, где вы ещё не оценивали, а здесь ваша
                  оценка уже есть:
                </p>
                <RunSummary run={run} />
              </>
            )}
            {freshHint}
          </>
        );
        actions = openOwn(run);
        break;
      case "parkrun_history":
        body = (
          <>
            <p className="rate-tab-lead">
              {lastVisit ? `Последний раз вы были здесь ${lastVisit}. ` : ""}
              Оценить можно старт за последние {windowDays}, а давние старты parkrun не
              оцениваются.
            </p>
            {freshHint}
          </>
        );
        break;
      case "few_runs":
        // Без глагола и падежа при числе: «у вас пока 4 из 5» читается при
        // любом пороге, а «осталось 1 пробежка» — нет.
        body = (
          <>
            <p className="rate-tab-lead">
              Оценивать старты можно с {status.min_runs_required}-й пробежки в истории — у вас
              пока {status.total_runs} из {status.min_runs_required}.
              {!lastVisit &&
                " К тому же на этой локации мы пока не нашли ваших стартов: оценить можно только старт, в котором вы участвовали."}
            </p>
            <p className="rate-tab-hint">
              Пробежки всех привязанных систем складываются: бегаете ещё где-то — привяжите и тот
              профиль и возвращайтесь по этой же ссылке.
            </p>
          </>
        );
        actions = profilesLink(false);
        break;
      case "syncing":
        body = (
          <p className="rate-tab-lead">
            Загружаем историю вашего профиля — обычно это несколько минут, у parkrun дольше. Как
            загрузится, ваш старт появится здесь.
          </p>
        );
        actions = retryButton("Проверить снова");
        break;
      case "no_profile":
        body = (
          <p className="rate-tab-lead">
            Чтобы оценить старт, привяжите профиль своей беговой системы — по нему мы найдём ваши
            старты на этой локации.
          </p>
        );
        actions = profilesLink(true, `/welcome?${LOGIN_NEXT_PARAM}=${encodeURIComponent(ratePath)}`);
        break;
      default:
        // not_here — и любой код, о котором эта версия страницы ещё не знает.
        // Привязка здесь — запасной путь (ревью 21.10.2026): чаще человек здесь
        // просто не бегал или протокол ещё не загружен, а система локации у
        // него уже привязана, и главной кнопкой она звала бы делать лишнее.
        body = (
          <>
            <p className="rate-tab-lead">
              Мы не нашли ваших стартов на этой локации — ни пробежек, ни волонтёрства. Оценить
              можно только старт, в котором вы участвовали.
            </p>
            <p className="rate-tab-hint">
              {protocolPending && `${FRESH_START_HINT} `}Бегали здесь в другой системе — привяжите
              её профиль и возвращайтесь по этой же ссылке.
            </p>
          </>
        );
        actions = profilesLink(false);
    }
  }

  return (
    <PortalSectionShell sidebar={{ active: "locations", location: sidebarLocation }}>
      <header className="loc-header loc-wide-page">
        <p className="muted loc-header-breadcrumb">
          <a href="/locations">← Все локации</a> / <a href={`/locations/${page.slug}`}>{page.name}</a> / Оценка
        </p>
        <div className="loc-header-title">
          <h1>{page.name} — оценка старта</h1>
        </div>
        {/* Анонимность — первой: на телефоне описание свёрнуто до двух строк,
            а человеку из чата это главное успокоение (ревью 22.10.2026). */}
        <p className="protocol-subtitle">
          По умолчанию оценка анонимна. Поставьте звёзды организации, трассе и атмосфере и добавьте
          пару слов о своём старте здесь — пробежке или волонтёрстве.
        </p>
      </header>

      <section className="card loc-section rate-tab" aria-labelledby="rate-tab-mine-title">
        <h2 className="section-title" id="rate-tab-mine-title">
          Ваша оценка старта
        </h2>
        {body}
        {actions && <div className="actions-row rate-tab-actions">{actions}</div>}
      </section>

      <section className="card loc-section rate-tab" aria-labelledby="rate-tab-share-title">
        <h2 className="section-title" id="rate-tab-share-title">
          Позовите других оценить
        </h2>
        <p className="rate-tab-lead">
          Отправьте ссылку в чат локации — каждый, кто откроет, попадёт сразу на оценку своего
          старта.
        </p>
        {/* Снекбар один на страницу: «Ссылка скопирована» сменяет «Оценка
            сохранена», а не ложится на неё в том же углу. */}
        <RateLinkShare slug={page.slug} locationName={page.name} onNotify={showSnackbar} />
      </section>

      {modalRun && (
        <RateRunModal
          run={modalRun}
          onClose={() => setModalRun(null)}
          onSaved={() => {
            setModalRun(null);
            refresh();
            showSnackbar({ variant: "default", title: "Спасибо!", message: "Оценка сохранена" });
          }}
          onDeleted={() => {
            setModalRun(null);
            refresh();
            showSnackbar({ variant: "default", title: "Готово", message: "Оценка удалена" });
          }}
        />
      )}
      <Snackbar open={snackbar.open} title={snackbar.title} variant={snackbar.variant} onDismiss={dismissSnackbar}>
        {snackbar.message}
      </Snackbar>
    </PortalSectionShell>
  );
}

export function LocationRatePage({ slug }: { slug: string }) {
  return <LocationRateContent slug={slug} />;
}
