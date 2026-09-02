import { useEffect, useState } from "react";
import { FilterSelect } from "../../components/filters/FilterPanel";
import { RequireAuth } from "../../components/RequireAuth";
import {
  ApiError,
  getOrganizerEventDates,
  getOrganizerEventPost,
  type OrganizerEventDateItem,
  type OrganizerPostTemplate,
} from "../../lib/api";
import { copyToClipboard } from "../../lib/clipboard";
import { formatDate, formatInt, platformCodeLabel } from "../../lib/format";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { locationHintFor } from "../../lib/locationHint";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { OrganizerBreadcrumbs } from "./OrganizerBreadcrumbs";
import { OrganizerDenied } from "./OrganizerDenied";
import "./organizer.css";

// Форматы — по анализу 21 телеграм-канала локаций: у каждой оргкоманды свой
// регулярный пост, задача — закрыть все (зеркало POST_TEMPLATES на бэкенде).
// needsEvent=false — пост строится по локации, селект события не нужен.
const TEMPLATES: {
  key: OrganizerPostTemplate;
  emoji: string;
  label: string;
  hint: string;
  needsEvent: boolean;
}[] = [
  {
    key: "full",
    emoji: "📋",
    label: "Сводный пост",
    hint: "Цифры, топ финишей, новички, рекорды, юбилеи и клубы",
    needsEvent: true,
  },
  {
    key: "stats",
    emoji: "📊",
    label: "Герои старта",
    hint: "Кого отметить: личники, новички, гости и юбилеи",
    needsEvent: true,
  },
  {
    key: "volunteers",
    emoji: "🤝",
    label: "Спасибо волонтёрам",
    hint: "Команда старта по ролям, с эмодзи каждой роли",
    needsEvent: true,
  },
  {
    key: "newcomers",
    emoji: "👑",
    label: "Привет новичкам",
    hint: "Первый финиш, первое волонтёрство и гости — поимённо",
    needsEvent: true,
  },
  {
    key: "milestones",
    emoji: "🎂",
    label: "Юбилеи дня",
    hint: "Юбилейные финиши и волонтёрства старта — по уровням",
    needsEvent: true,
  },
  {
    key: "upcoming",
    emoji: "🔮",
    label: "Юбилеи завтра",
    hint: "Пятничная рубрика: чей юбилей случится на ближайшем старте",
    needsEvent: false,
  },
  {
    key: "vacancies",
    emoji: "🙌",
    label: "Нужны волонтёры",
    hint: "Свободные позиции ближайшего старта — по живой записи 5 вёрст",
    needsEvent: false,
  },
  {
    key: "travelers",
    emoji: "🧭",
    label: "Наши в гостях",
    hint: "Кто из своих бегал в эту субботу в других парках",
    needsEvent: true,
  },
];

function eventOptionLabel(item: OrganizerEventDateItem): string {
  const number = item.event_number ? `№${item.event_number} · ` : "";
  return `${number}${formatDate(item.event_date)} · ${platformCodeLabel(item.platform_code)} · ${formatInt(item.finishers_count)} фин.`;
}

function OrganizerPostContent({ slug }: { slug: string }) {
  const [dates, setDates] = useState<OrganizerEventDateItem[] | null>(null);
  const [locationName, setLocationName] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [template, setTemplate] = useState<OrganizerPostTemplate>("full");
  // Пороги «Юбилеев завтра»: отдельно пробежки и волонтёрства.
  const [minRunMilestone, setMinRunMilestone] = useState(10);
  const [minVolMilestone, setMinVolMilestone] = useState(10);
  // «Наши в гостях»: от скольких финишей у нас человек считается своим.
  const [travelersMinRuns, setTravelersMinRuns] = useState(5);
  const [postText, setPostText] = useState<string | null>(null);
  // «Без жирного»: вырезает **разметку** — Телеграм её рендерит, а ВК нет.
  // Выбор человека переживает перезаходы (localStorage).
  const [plainMode, setPlainMode] = useState(() => {
    try {
      return localStorage.getItem("org-post-plain") === "1";
    } catch {
      return false;
    }
  });
  const [postLoading, setPostLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [copied, setCopied] = useState(false);

  const activeTemplate = TEMPLATES.find((item) => item.key === template) ?? TEMPLATES[0];

  useEffect(() => {
    let cancelled = false;
    getOrganizerEventDates(slug)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setDates(payload.items);
        setLocationName(payload.location.name);
        if (payload.items.length > 0) {
          setSelectedEventId(payload.items[0].event_id);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
        } else if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "Не удалось загрузить события");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const needsEvent = activeTemplate.needsEvent;

  useEffect(() => {
    if (needsEvent && !selectedEventId) {
      return;
    }
    let cancelled = false;
    setPostLoading(true);
    setPostText(null);
    setCopied(false);
    // Сброс прошлой ошибки: без него один упавший запрос гасил страницу
    // навсегда — контролы размонтированы, зависимости эффекта меняться нечем.
    setError(null);
    getOrganizerEventPost(slug, needsEvent ? selectedEventId : null, template, {
      minRunMilestone,
      minVolMilestone,
      travelersMinRuns,
    })
      .then((payload) => {
        if (!cancelled) {
          setPostText(payload.post_text);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось собрать пост");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPostLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, selectedEventId, template, needsEvent, minRunMilestone, minVolMilestone, travelersMinRuns]);

  const displayText = postText === null ? null : plainMode ? postText.replace(/\*\*/g, "") : postText;

  const togglePlainMode = () => {
    setPlainMode((current) => {
      const next = !current;
      try {
        localStorage.setItem("org-post-plain", next ? "1" : "0");
      } catch {
        // localStorage недоступен — просто не запоминаем
      }
      return next;
    });
  };

  const handleCopy = async () => {
    if (!displayText) {
      return;
    }
    if (await copyToClipboard(displayText)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  const name = locationName ?? locationHintFor(slug)?.name ?? null;
  const sidebar = {
    active: "organizer" as const,
    location: name ? { slug, name } : locationHintFor(slug),
  };

  if (forbidden || notFound) {
    return (
      <PortalSectionShell sidebar={sidebar}>
        <OrganizerDenied slug={slug} notFound={notFound} />
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={sidebar}>
      <header className="loc-header">
        <OrganizerBreadcrumbs slug={slug} locationName={name} tool="Пост-отчёт" />
        <div className="loc-header-title">
          <h1>{name ?? "Локация"} — пост-отчёт</h1>
        </div>
        <p className="muted">
          Готовые посты для чата или канала локации: выберите формат, проверьте текст и
          скопируйте. Форматы собраны по практике оргкоманд двадцати локаций.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && dates === null && <p className="muted">Загрузка…</p>}

      {dates !== null && (
        <div className="org-post-layout">
          <aside className="org-post-formats" role="tablist" aria-label="Формат поста">
            {TEMPLATES.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={template === item.key}
                className={`org-format-card${template === item.key ? " active" : ""}`}
                onClick={() => setTemplate(item.key)}
              >
                <span className="org-format-emoji" aria-hidden="true">
                  {item.emoji}
                </span>
                <span className="org-format-text">
                  <span className="org-format-title">{item.label}</span>
                  <span className="org-format-hint">{item.hint}</span>
                </span>
              </button>
            ))}
          </aside>

          <div className="org-post-main">
            <section className="card org-post-controls">
              <div className="org-toolbar-row">
                {needsEvent && dates.length > 0 && (
                  <label className="org-toolbar-label">
                    Событие{" "}
                    <FilterSelect
                      ariaLabel="Событие"
                      value={selectedEventId ?? ""}
                      onChange={(value) => setSelectedEventId(String(value))}
                      options={dates.map((item) => ({ value: item.event_id, label: eventOptionLabel(item) }))}
                    />
                  </label>
                )}
                {template === "upcoming" && (
                  <>
                    <label className="org-toolbar-label">
                      Пробежки от{" "}
                      <FilterSelect
                        ariaLabel="Пробежки от"
                        value={minRunMilestone}
                        onChange={setMinRunMilestone}
                        options={[10, 25, 50, 100].map((value) => ({ value, label: `${value}-й` }))}
                      />
                    </label>
                    <label className="org-toolbar-label">
                      Волонтёрства от{" "}
                      <FilterSelect
                        ariaLabel="Волонтёрства от"
                        value={minVolMilestone}
                        onChange={setMinVolMilestone}
                        options={[10, 25, 50, 100].map((value) => ({ value, label: `${value}-го` }))}
                      />
                    </label>
                    <span className="muted">Публикуйте в пятницу — про завтрашний старт.</span>
                  </>
                )}
                {template === "travelers" && (
                  <label className="org-toolbar-label">
                    Свои — от{" "}
                    <FilterSelect
                      ariaLabel="Свои — от скольких финишей"
                      value={travelersMinRuns}
                      onChange={setTravelersMinRuns}
                      options={[3, 5, 10, 20].map((value) => ({ value, label: `${value} финишей у нас` }))}
                    />
                  </label>
                )}
                {template === "vacancies" && (
                  <span className="muted">
                    Роли и записавшиеся — с официальной страницы записи волонтёров:{" "}
                    <a
                      href={`https://5verst.ru/${slug}/volunteer/`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      5verst.ru/{slug}/volunteer
                    </a>
                  </span>
                )}
                {template === "full" && (
                  <span className="muted">Самый подробный формат — всё в одном посте.</span>
                )}
              </div>
            </section>

            <section className="card org-post-preview">
              <header className="org-post-preview-head">
                <span className="org-post-preview-title">
                  {activeTemplate.emoji} {activeTemplate.label}
                  <span className="muted org-post-preview-sub"> · предпросмотр</span>
                </span>
                <span className="org-post-preview-actions">
                  <label
                    className="org-toolbar-checkbox muted"
                    title="Убирает жирную разметку (**текст**) — для площадок, которые её не понимают"
                  >
                    <input type="checkbox" checked={plainMode} onChange={togglePlainMode} />
                    без разметки
                  </label>
                  <button
                    type="button"
                    className="btn secondary btn-sm"
                    disabled={!postText}
                    onClick={() => void handleCopy()}
                  >
                    {copied ? "Скопировано ✓" : "Скопировать пост"}
                  </button>
                </span>
              </header>
              {postLoading && (
                <div className="org-post-skeleton" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                </div>
              )}
              {!postLoading && displayText !== null && (
                <div className="org-post-bubble">{displayText}</div>
              )}
              {!postLoading && postText === null && needsEvent && dates.length === 0 && (
                <p className="muted">У локации пока нет событий с протоколами.</p>
              )}
            </section>
          </div>
        </div>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerPostPage({ slug }: { slug: string }) {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {() => <OrganizerPostContent slug={slug} />}
    </RequireAuth>
  );
}
