import { useEffect, useState } from "react";
import { ShareIcon } from "./ShareIcon";
import { PlatformBadge } from "./PlatformBadge";
import { DetailModal } from "./DetailModal";
import type { OnThisDay, OnThisDayRun } from "../lib/api";
import { formatDateLong, formatFinishTimeValue, formatInt, pluralFormRu } from "../lib/format";
import { useOptionalUser } from "../lib/useOptionalUser";
import { useOptionalShareSheet } from "../features/sharing/ShareSheetContext";
import { onThisDaySubject } from "../features/sharing/subjects";
import { ourProtocolHref } from "../lib/protocolHref";

type OnThisDayCardProps = {
  load: () => Promise<OnThisDay>;
};

const YEAR_FORMS = ["год", "года", "лет"] as const;
const RUN_FORMS = ["пробежка", "пробежки", "пробежек"] as const;
// localStorage: today_iso, на который карточку закрыли (скрываем до следующего дня).
const ON_THIS_DAY_DISMISS_KEY = "onThisDayDismissedDate";

// Компактное время: «00:23:04» → «23:04».
function compactTime(display: string | null, sec: number | null): string | null {
  const value = formatFinishTimeValue(display, sec);
  if (!value || value === "—") {
    return null;
  }
  return value.replace(/^00:/, "");
}

// «год назад» / «2 года назад» / «5 лет назад».
function yearsAgoPhrase(years: number): string {
  if (years <= 0) {
    return "сегодня";
  }
  if (years === 1) {
    return "год назад";
  }
  return `${years} ${pluralFormRu(years, YEAR_FORMS)} назад`;
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// «23:04 · 6 место» — детали одной пробежки.
function runDetailsLine(run: OnThisDayRun): string {
  const parts: string[] = [];
  const time = compactTime(run.finish_time_display, run.finish_time_sec);
  if (time) {
    parts.push(time);
  }
  if (run.position != null) {
    parts.push(`${run.position} место`);
  }
  return parts.join(" · ");
}


function RunLocation({ run }: { run: OnThisDayRun }) {
  const internal = ourProtocolHref(run);
  if (internal) {
    return (
      <a
        href={internal}
        className="on-this-day-location"
        title="Открыть протокол старта"
        onClick={(event) => event.stopPropagation()}
      >
        {run.location_name}
      </a>
    );
  }
  return run.event_url ? (
    <a
      href={run.event_url}
      target="_blank"
      rel="noreferrer"
      className="on-this-day-location"
      onClick={(event) => event.stopPropagation()}
    >
      {run.location_name}
    </a>
  ) : (
    <span className="on-this-day-location">{run.location_name}</span>
  );
}

export function OnThisDayCard({ load }: OnThisDayCardProps) {
  const [data, setData] = useState<OnThisDay | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const sheet = useOptionalShareSheet();
  const user = useOptionalUser();
  // Дата (today_iso), на которую карточку закрыли крестиком. Пока совпадает с
  // сегодняшней — карточку не показываем; на следующий день today_iso другой →
  // карточка снова появится со свежими данными.
  const [dismissedDate, setDismissedDate] = useState<string | null>(() => {
    try {
      return localStorage.getItem(ON_THIS_DAY_DISMISS_KEY);
    } catch {
      return null;
    }
  });

  useEffect(() => {
    let cancelled = false;
    load()
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch(() => {
        // тихо — карточка просто не покажется
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  if (!data || !data.run || data.kind === null) {
    return null;
  }
  if (dismissedDate === data.today_iso) {
    return null;
  }

  const run = data.run;
  const todayIso = data.today_iso;
  const dismiss = () => {
    try {
      localStorage.setItem(ON_THIS_DAY_DISMISS_KEY, todayIso);
    } catch {
      // приватный режим — просто скроем на эту сессию
    }
    setDismissedDate(todayIso);
  };
  const runs = data.runs.length > 0 ? data.runs : [run];
  const otherCount = Math.max(runs.length - 1, data.also_count);
  const hasMore = otherCount > 0;

  // «Поделиться» открывает шторку с этой пробежкой — без навигации.
  const openShare = (target: OnThisDayRun) => {
    sheet?.open({ subject: onThisDaySubject(target, user ?? null), entry: "on_this_day" });
  };

  const detailsLine = runDetailsLine(run);

  return (
    <>
      <section
        className={`on-this-day on-this-day-anniversary${hasMore ? " on-this-day-clickable" : ""}`}
        aria-label="В этот день"
        onClick={hasMore ? () => setModalOpen(true) : undefined}
        role={hasMore ? "button" : undefined}
        tabIndex={hasMore ? 0 : undefined}
        onKeyDown={
          hasMore
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setModalOpen(true);
                }
              }
            : undefined
        }
      >
        <button
          type="button"
          className="on-this-day-close"
          aria-label="Скрыть до завтра"
          title="Скрыть до завтра"
          onClick={(event) => {
            event.stopPropagation();
            dismiss();
          }}
        >
          ×
        </button>
        <div className="on-this-day-body">
          <p className="on-this-day-headline">
            В этот день {yearsAgoPhrase(run.years_ago)}{" "}
            <span className="on-this-day-date">({formatDateLong(run.event_date)})</span>
          </p>
          <p className="on-this-day-run">
            <RunLocation run={run} />
            <PlatformBadge code={run.platform_code} />
            {run.is_pr && <span className="on-this-day-pr">PR</span>}
            {detailsLine && <span className="on-this-day-details">{detailsLine}</span>}
          </p>
          {hasMore && (
            <span className="on-this-day-more">
              И ещё {formatInt(otherCount)} {pluralFormRu(otherCount, RUN_FORMS)} в этот день — показать все
            </span>
          )}
        </div>
        <button
          type="button"
          className="share-cta share-cta-on-dark"
          onClick={(event) => {
            event.stopPropagation();
            openShare(run);
          }}
        >
          <ShareIcon />
          Поделиться
        </button>
      </section>

      <DetailModal open={modalOpen} title="Пробежки в этот день" onClose={() => setModalOpen(false)}>
        <ul className="on-this-day-list">
          {runs.map((item, index) => {
            const line = runDetailsLine(item);
            return (
              <li key={`${item.event_date}-${item.platform_code}-${index}`} className="on-this-day-list-item">
                <div className="on-this-day-list-info">
                  <p className="on-this-day-list-when">
                    {capitalize(yearsAgoPhrase(item.years_ago))} · {formatDateLong(item.event_date)}
                  </p>
                  <p className="on-this-day-list-run">
                    <RunLocation run={item} />
                    <PlatformBadge code={item.platform_code} />
                    {item.is_pr && <span className="on-this-day-list-pr">PR</span>}
                    {line && <span className="on-this-day-list-details">{line}</span>}
                  </p>
                </div>
                <button
                  type="button"
                  className="on-this-day-list-share"
                  onClick={() => openShare(item)}
                  aria-label="Поделиться"
                  title="Поделиться"
                >
                  <ShareIcon />
                </button>
              </li>
            );
          })}
        </ul>
      </DetailModal>
    </>
  );
}
