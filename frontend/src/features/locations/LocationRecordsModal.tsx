import { useEffect, useState } from "react";
import { ActivityDateLink } from "../../components/ActivityDateLink";
import { DetailModal } from "../../components/DetailModal";
import { PlatformBadge } from "../../components/PlatformBadge";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { getLocationEvents, type LocationEventRow } from "../../lib/api";

export type RecordType = "attendance" | "male" | "female";

type RecordRow = {
  key: string;
  event_date: string;
  value: string;
  runnerName: string | null;
  platform_code: string;
  protocol_url: string | null;
  isGlobal: boolean;
};

const MODAL_TITLE: Record<RecordType, string> = {
  attendance: "Рекорд посещаемости",
  male: "Рекорд трассы · М",
  female: "Рекорд трассы · Ж",
};

const HINT_TEXT: Record<RecordType, string> = {
  attendance:
    "Рекорд в системе — лучшая посещаемость среди стартов конкретной платформы на тот момент. " +
    "Оранжевым выделено значение — рекорд посещаемости по всей истории локации сквозь все платформы на тот момент.",
  male:
    "Рекорд в системе — лучшее время мужчин в конкретной платформе на тот момент. " +
    "Оранжевым выделено значение — рекорд трассы среди мужчин по всей истории локации сквозь все платформы.",
  female:
    "Рекорд в системе — лучшее время женщин в конкретной платформе на тот момент. " +
    "Оранжевым выделено значение — рекорд трассы среди женщин по всей истории локации сквозь все платформы.",
};

const GLOBAL_TOOLTIP: Record<RecordType, string> = {
  attendance: "Рекорд посещаемости по всей истории локации сквозь все платформы на этот момент.",
  male: "Рекорд трассы среди мужчин по всей истории локации сквозь все платформы на этот момент.",
  female: "Рекорд трассы среди женщин по всей истории локации сквозь все платформы на этот момент.",
};

function stripHours(display: string | null): string {
  if (!display) {
    return "—";
  }
  return display.replace(/^00:/, "");
}

function isPlatformRecord(item: LocationEventRow, type: RecordType): boolean {
  if (type === "attendance") return item.is_platform_attendance_record;
  if (type === "male") return item.is_platform_course_record_male;
  return item.is_platform_course_record_female;
}

function isGlobalRecord(item: LocationEventRow, type: RecordType): boolean {
  if (type === "attendance") return item.is_attendance_record;
  if (type === "male") return item.is_course_record_male;
  return item.is_course_record_female;
}

function valueFor(item: LocationEventRow, type: RecordType): string {
  if (type === "attendance") {
    return `${item.finishers ?? "—"} финишеров`;
  }
  if (type === "male") {
    return stripHours(item.best_male_time_display);
  }
  return stripHours(item.best_female_time_display);
}

function runnerNameFor(item: LocationEventRow, type: RecordType): string | null {
  if (type === "male") {
    return item.best_male_runner_name;
  }
  if (type === "female") {
    return item.best_female_runner_name;
  }
  return null;
}

function buildRows(items: LocationEventRow[], type: RecordType): RecordRow[] {
  const rows: RecordRow[] = [];
  for (const item of items) {
    if (!isPlatformRecord(item, type)) {
      continue;
    }
    rows.push({
      key: `${item.event_date}-${item.platform_code}`,
      event_date: item.event_date,
      value: valueFor(item, type),
      runnerName: runnerNameFor(item, type),
      platform_code: item.platform_code,
      protocol_url: item.protocol_url,
      isGlobal: isGlobalRecord(item, type),
    });
  }
  rows.sort((a, b) => (a.event_date < b.event_date ? 1 : -1));
  return rows;
}

export function LocationRecordsModal({
  slug,
  type,
  open,
  onClose,
}: {
  slug: string;
  type: RecordType;
  open: boolean;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<RecordRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const showRunnerColumn = type === "male" || type === "female";

  useEffect(() => {
    if (!open) {
      return;
    }
    setRows(null);
    setError(null);
    let cancelled = false;
    getLocationEvents(slug)
      .then((data) => {
        if (!cancelled) {
          setRows(buildRows(data.items, type));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить рекорды");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, slug, type]);

  return (
    <DetailModal open={open} title={MODAL_TITLE[type]} onClose={onClose}>
      {error && <p className="error-text">{error}</p>}
      {!error && !rows && <p className="muted">Загрузка…</p>}
      {!error && rows && rows.length === 0 && <p className="muted unique-locations-empty">Нет данных.</p>}
      {!error && rows && rows.length > 0 && (
        <>
          <p className="muted personal-records-hint">{HINT_TEXT[type]}</p>
          <div className="unique-locations-table-wrap">
            <table className="data-table unique-locations-table best-results-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  {showRunnerColumn && <th>ФИО</th>}
                  <th>Значение</th>
                  <th>Система</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key}>
                    <td className="col-date">
                      <ActivityDateLink date={row.event_date} url={row.protocol_url} />
                    </td>
                    {showRunnerColumn && <td className="col-runner">{row.runnerName ?? "—"}</td>}
                    <td className="col-result">
                      {row.isGlobal ? (
                        <StatHintTooltip text={GLOBAL_TOOLTIP[type]} className="finish-time-global-pr-tooltip">
                          <span className="finish-time-global-pr">{row.value}</span>
                        </StatHintTooltip>
                      ) : (
                        row.value
                      )}
                    </td>
                    <td className="col-platform">
                      <PlatformBadge code={row.platform_code} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </DetailModal>
  );
}
