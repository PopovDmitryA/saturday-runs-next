import { useEffect, useMemo, useState } from "react";
import { ShareIcon } from "../../components/ShareIcon";
import { PlatformBadge } from "../../components/PlatformBadge";
import {
  getDashboard,
  type MyHistory,
  type MyHistoryMilestone,
} from "../../lib/api";
import { formatDate, formatDateLong, pluralFormRu } from "../../lib/format";
import { useOptionalUser } from "../../lib/useOptionalUser";
import { useOptionalShareSheet } from "../sharing/ShareSheetContext";
import { milestoneSubject } from "../sharing/subjects";
import {
  milestoneBragText,
  runNumberLabel,
  saturdayStreakLabel,
  volunteerNumberLabel,
} from "./milestoneShare";
import { ourProtocolHref } from "../../lib/protocolHref";

const MILESTONE_FORMS = ["веха", "вехи", "вех"] as const;
const YEAR_FORMS = ["год", "года", "лет"] as const;

// Юбилейный порядковый номер региона/города: 3, 5, 10, 15, 20, 25, 30…
// (правило продублировано с бэкендом — is_geo_milestone_number).
export function isGeoMilestoneNumber(number: number | null): boolean {
  if (number == null) {
    return false;
  }
  return number === 3 || number === 5 || (number >= 10 && number % 5 === 0);
}


type MilestoneVisual = {
  icon: string;
  className: string;
};

// Иконка и цветовая тема маркера на линии времени. У платформенных пар
// (сквозная веха / веха по системе) — общий цвет темы, разная иконка: badge
// платформы рядом и так покажет систему, а иконка лишь намекает на разницу.
export function milestoneVisual(milestone: MyHistoryMilestone): MilestoneVisual {
  switch (milestone.kind) {
    case "first_run":
      return { icon: "🏁", className: "history-kind-start" };
    case "first_run_platform":
      return { icon: "🚩", className: "history-kind-start" };
    case "run_club":
      return { icon: "🏅", className: "history-kind-club" };
    case "run_club_platform":
      return { icon: "🎖️", className: "history-kind-club" };
    case "location_club":
      return { icon: "🎯", className: "history-kind-location-club" };
    case "volunteer_location_club":
      return { icon: "📍", className: "history-kind-location-club" };
    case "first_foreign_parkrun":
    case "first_foreign_run":
      return { icon: "✈️", className: "history-kind-geo" };
    case "global_pr":
      return { icon: "🏆", className: "history-kind-pr-global" };
    case "pr":
      return { icon: "⚡", className: "history-kind-pr" };
    case "location_pr":
      return { icon: "🥉", className: "history-kind-pr-location" };
    case "location_course_record":
      return { icon: "👑", className: "history-kind-pr-global" };
    case "location_age_group_record":
      return { icon: "🏵️", className: "history-kind-pr-location" };
    case "new_country":
      return { icon: "🌍", className: "history-kind-geo" };
    case "new_region":
      return { icon: "🧭", className: "history-kind-geo" };
    case "new_city":
      return { icon: "🏙️", className: "history-kind-geo" };
    case "new_location":
      return { icon: "🗺️", className: "history-kind-geo" };
    case "first_volunteer":
    case "volunteer_club":
      return { icon: "🤝", className: "history-kind-volunteer" };
    case "volunteer_club_platform":
      return { icon: "🙌", className: "history-kind-volunteer" };
    case "saturday_streak":
      return { icon: "🔥", className: "history-kind-streak" };
    case "saturday_run_streak":
      return { icon: "🏃", className: "history-kind-streak" };
    case "saturday_volunteer_streak":
      return { icon: "🙋", className: "history-kind-streak" };
    default:
      return { icon: "⭐", className: "history-kind-anniversary" };
  }
}

// «00:26:43» → «26:43».
function compactTime(display: string | null): string | null {
  if (!display) {
    return null;
  }
  return display.replace(/^00:/, "");
}

// «−52 сек» / «−1:44» — на сколько улучшен рекорд.
function deltaLabel(deltaSec: number): string {
  if (deltaSec < 60) {
    return `−${deltaSec} сек`;
  }
  const minutes = Math.floor(deltaSec / 60);
  const seconds = deltaSec % 60;
  return `−${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function milestoneTitle(milestone: MyHistoryMilestone): string {
  const time = compactTime(milestone.finish_time_display);
  switch (milestone.kind) {
    case "first_run":
      return "Первая пробежка";
    case "first_run_platform":
      return "Первый старт в системе";
    case "run_club":
      return `Клуб ${milestone.number}`;
    case "run_club_platform":
      return `Клуб ${milestone.number} в системе`;
    case "location_club":
      return `${runNumberLabel(milestone.number ?? 0)} пробежка в локации`;
    case "volunteer_location_club":
      return `${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство в локации`;
    case "first_foreign_parkrun":
      return "Первый зарубежный паркран";
    case "first_foreign_run":
      return milestone.country
        ? `Первый зарубежный старт: ${milestone.country}`
        : "Первый зарубежный старт";
    case "global_pr":
      return time ? `Глобальный рекорд — ${time}` : "Глобальный рекорд";
    case "pr":
      return time ? `Личный рекорд в системе — ${time}` : "Личный рекорд в системе";
    case "location_pr":
      return time ? `Личный рекорд в локации — ${time}` : "Личный рекорд в локации";
    case "location_course_record": {
      const base =
        milestone.record_scope === "global"
          ? "Глобальный рекорд локации"
          : milestone.record_scope
            ? "Рекорд локации в системе"
            : "Рекорд локации";
      return time ? `${base} — ${time}` : base;
    }
    case "location_age_group_record": {
      const base = milestone.age_group
        ? `Рекорд локации в группе ${milestone.age_group}`
        : "Рекорд локации в возрастной группе";
      return time ? `${base} — ${time}` : base;
    }
    case "new_country":
      return `Новая страна: ${milestone.country ?? milestone.location_name}`;
    case "new_region":
      return isGeoMilestoneNumber(milestone.number)
        ? `${milestone.number}-й регион: ${milestone.region ?? milestone.location_name}`
        : `Новый регион: ${milestone.region ?? milestone.location_name}`;
    case "new_city":
      return isGeoMilestoneNumber(milestone.number)
        ? `${milestone.number}-й город: ${milestone.location_city ?? milestone.location_name}`
        : `Новый город: ${milestone.location_city ?? milestone.location_name}`;
    case "new_location":
      // Название локации показывает соседняя ссылка (MilestoneLocation) —
      // в заголовке не дублируем, иначе имя площадки повторяется дважды.
      return isGeoMilestoneNumber(milestone.number)
        ? `${milestone.number}-я новая локация`
        : "Новая локация";
    case "first_volunteer":
      return "Первое волонтёрство";
    case "volunteer_club":
      return `${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство`;
    case "volunteer_club_platform":
      return `${volunteerNumberLabel(milestone.number ?? 0)} волонтёрство в системе`;
    case "saturday_streak":
      return `Рекорд серии — ${saturdayStreakLabel(milestone.number)}`;
    case "saturday_run_streak":
      return `Рекорд серии пробежек — ${saturdayStreakLabel(milestone.number)}`;
    case "saturday_volunteer_streak":
      return `Рекорд серии волонтёрств — ${saturdayStreakLabel(milestone.number)}`;
    default:
      return "Веха";
  }
}

// Подзаголовок-пояснение для вех, где полезен контекст.
function milestoneHint(milestone: MyHistoryMilestone): string | null {
  switch (milestone.kind) {
    case "run_club":
    case "run_club_platform":
      return `${milestone.number}-я пробежка`;
    case "location_club":
    case "volunteer_location_club":
      return `Клуб ${milestone.number}`;
    case "global_pr":
    case "pr":
    case "location_pr":
      return milestone.delta_sec != null ? deltaLabel(milestone.delta_sec) : null;
    case "location_course_record":
      // Пояснение уровня нужно только мультисистемным площадкам (у них охват
      // проставлен); у монолокаций record_scope пуст — и пояснять нечего.
      if (milestone.record_scope === "global") {
        return "лучшее время сквозь все системы площадки";
      }
      if (milestone.record_scope) {
        return "лучшее время площадки в этой системе";
      }
      return "лучшее время площадки";
    case "location_age_group_record":
      return "лучшее время площадки в возрастной группе";
    default:
      return null;
  }
}


function MilestoneLocation({ milestone }: { milestone: MyHistoryMilestone }) {
  // Внутрь сайта, на наш протокол этого старта; наружу — только если своей
  // страницы у старта быть не может (тестовый старт, локация без слага).
  const internal = ourProtocolHref(milestone);
  if (internal) {
    return (
      <a href={internal} className="history-location" title="Открыть протокол старта">
        {milestone.location_name}
      </a>
    );
  }
  return milestone.event_url ? (
    <a href={milestone.event_url} target="_blank" rel="noreferrer" className="history-location">
      {milestone.location_name}
    </a>
  ) : (
    <span className="history-location">{milestone.location_name}</span>
  );
}

function MilestoneShareButton({ milestone }: { milestone: MyHistoryMilestone }) {
  const sheet = useOptionalShareSheet();
  const user = useOptionalUser();
  if (sheet === null) {
    return null;
  }
  return (
    <button
      type="button"
      className="history-share"
      title="Сделать картинку-сториз с этой вехой"
      aria-label="Поделиться вехой"
      onClick={() => sheet.open({ subject: milestoneSubject(milestone, user ?? null), entry: "history" })}
    >
      <ShareIcon />
      <span className="history-share-label">Поделиться</span>
    </button>
  );
}

function BragIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
    </svg>
  );
}

// Текст-хвастовство: достижение + ссылка на сайт, готовые для пересылки в
// Telegram и подобные каналы (в отличие от «Поделиться» — не картинка, а текст).
function MilestoneBragButton({
  milestone,
  siteUrl,
}: {
  milestone: MyHistoryMilestone;
  siteUrl: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(milestoneBragText(milestone, siteUrl));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API недоступна (приватный режим и т.п.) — тихо игнорируем
    }
  };

  return (
    <button
      type="button"
      className="history-share history-brag"
      title="Скопировать текст с достижением и ссылкой на сайт"
      aria-label="Скопировать текст вехи"
      onClick={() => void handleCopy()}
    >
      <BragIcon />
      <span className="history-share-label">{copied ? "Скопировано ✓" : "Текст"}</span>
    </button>
  );
}

function MilestoneCard({
  milestone,
  shareBase,
  siteUrl,
}: {
  milestone: MyHistoryMilestone;
  // Отсутствуют на публичном профиле — там нельзя «поделиться» чужим
  // достижением как своим, кнопки просто не показываем.
  shareBase?: string;
  siteUrl?: string;
}) {
  const visual = milestoneVisual(milestone);
  const hint = milestoneHint(milestone);
  const time = compactTime(milestone.finish_time_display);
  // Веха рекордной серии — про недели подряд, а не про отдельный старт: время и
  // место той пробежки, что попала на дату рекорда, к достижению отношения не
  // имеют и только сбивают с толку.
  const isStreak = milestone.kind.startsWith("saturday_");
  const detailParts: string[] = [];
  // Для PR/глобального/локационного рекорда время уже в заголовке — в деталях не дублируем.
  if (
    time &&
    !isStreak &&
    milestone.kind !== "pr" &&
    milestone.kind !== "global_pr" &&
    milestone.kind !== "location_pr"
  ) {
    detailParts.push(time);
  }
  if (milestone.position != null && !isStreak) {
    detailParts.push(`${milestone.position} место`);
  }
  if (milestone.role) {
    detailParts.push(milestone.role);
  }

  return (
    <li className={`history-item ${visual.className}`}>
      <span className="history-marker" aria-hidden="true">
        {visual.icon}
      </span>
      <div className="history-card">
        <p className="history-line">
          <span className="history-title">{milestoneTitle(milestone)}</span>
          {hint && <span className="history-hint">{hint}</span>}
          <MilestoneLocation milestone={milestone} />
          <PlatformBadge code={milestone.platform_code} />
          {detailParts.length > 0 && (
            <span className="history-details-line">{detailParts.join(" · ")}</span>
          )}
        </p>
        <span className="history-line-right">
          {/* Длинная дата на десктопе, короткая («04.09.2021») на узких экранах. */}
          <span className="history-date-inline history-date-full">
            {formatDateLong(milestone.event_date)}
          </span>
          <span className="history-date-inline history-date-short">
            {formatDate(milestone.event_date)}
          </span>
          {siteUrl && <MilestoneBragButton milestone={milestone} siteUrl={siteUrl} />}
          {shareBase && <MilestoneShareButton milestone={milestone} />}
        </span>
      </div>
    </li>
  );
}

type HistoryContentProps = {
  load: () => Promise<MyHistory>;
  // Нет на публичном профиле — там нет своего мастера «Поделиться» для
  // чужих вех.
  shareBase?: string;
  siteUrl?: string;
  title?: string;
  description?: string;
  emptyText?: string;
};

export function HistoryContent({
  load,
  shareBase,
  siteUrl,
  title = "Моя история",
  description = "Ключевые вехи вашей беговой истории: первая пробежка, клубы, личные рекорды, рекорды серий суббот, новые регионы и волонтёрство. У каждой вехи — кнопка «Поделиться» с картинкой для сториз.",
  emptyText = "Пока нет вех — привяжите профиль беговой системы на главной, и история соберётся автоматически.",
}: HistoryContentProps) {
  const [data, setData] = useState<MyHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    load()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить историю");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  // Хронология от первой пробежки к сегодняшнему дню, с маркерами годов.
  const byYear = useMemo(() => {
    const milestones = data?.milestones ?? [];
    const groups: { year: number; items: MyHistoryMilestone[] }[] = [];
    for (const milestone of milestones) {
      const year = Number(milestone.event_date.slice(0, 4));
      const last = groups[groups.length - 1];
      if (last && last.year === year) {
        last.items.push(milestone);
      } else {
        groups.push({ year, items: [milestone] });
      }
    }
    return groups;
  }, [data]);

  const summary = useMemo(() => {
    const milestones = data?.milestones ?? [];
    if (milestones.length === 0) {
      return null;
    }
    const firstYear = Number(milestones[0].event_date.slice(0, 4));
    const lastYear = Number(milestones[milestones.length - 1].event_date.slice(0, 4));
    const years = Math.max(Math.abs(lastYear - firstYear) + 1, 1);
    const count = milestones.length;
    return `${count} ${pluralFormRu(count, MILESTONE_FORMS)} за ${years} ${pluralFormRu(years, YEAR_FORMS)}`;
  }, [data]);

  return (
    <>
      <section className="card history-intro">
        <h2 className="section-title">{title}</h2>
        <p className="muted">{description}</p>
        {summary && <p className="history-summary">{summary}</p>}
      </section>

      {loading && <p className="muted">Загрузка…</p>}

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && byYear.length === 0 && (
        <div className="card">
          <p className="muted">{emptyText}</p>
        </div>
      )}

      {!loading && !error && byYear.length > 0 && (
        <div className="history-timeline">
          {byYear.map((group) => (
            <section key={group.year} className="history-year">
              <h3 className="history-year-label">{group.year}</h3>
              <ul className="history-list">
                {group.items.map((milestone, index) => (
                  <MilestoneCard
                    key={`${milestone.event_date}-${milestone.kind}-${index}`}
                    milestone={milestone}
                    shareBase={shareBase}
                    siteUrl={siteUrl}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

// Ссылка на сайт для текста-хвастовства: публичный профиль, если он есть и
// открыт; пока не загрузился (или профиля нет) — просто корень сайта.
// Экспорт: хук переиспользуется в портальном ЛК (/new/history).
export function useOwnSiteUrl(): string {
  const [siteUrl, setSiteUrl] = useState(() => window.location.origin);

  useEffect(() => {
    let cancelled = false;
    getDashboard()
      .then((dashboard) => {
        if (cancelled) {
          return;
        }
        const handle = dashboard.public_slug ?? dashboard.serial_id;
        if (handle != null) {
          setSiteUrl(`${window.location.origin}/users/${handle}`);
        }
      })
      .catch(() => {
        // не удалось получить профиль — остаётся ссылка на корень сайта
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return siteUrl;
}

