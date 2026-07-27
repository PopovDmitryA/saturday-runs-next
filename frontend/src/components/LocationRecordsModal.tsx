import { type LocationRecordsBlock, type LocationRecordEntry } from "../lib/api";
import { formatDate, platformCodeLabel } from "../lib/format";
import { DetailModal } from "./DetailModal";
import { PlatformBadge } from "./PlatformBadge";

type RecordsKind = "course" | "age_group";

type LocationRecordsModalProps = {
  open: boolean;
  onClose: () => void;
  kind: RecordsKind;
  block: LocationRecordsBlock | undefined;
};

function levelLabel(entry: LocationRecordEntry): string | null {
  // Уровень есть только у площадок, живших в нескольких системах: «глобальный» —
  // лучшее время сквозь все системы, иначе — рекорд конкретной системы.
  if (!entry.level) {
    return null;
  }
  if (entry.level === "global") {
    return "глобальный";
  }
  return `в системе ${platformCodeLabel(entry.level)}`;
}

function locationCell(entry: LocationRecordEntry) {
  const name = entry.location_slug ? (
    <a className="unique-locations-name" href={`/locations/${entry.location_slug}`}>
      {entry.location_name}
    </a>
  ) : (
    <span className="unique-locations-name">{entry.location_name}</span>
  );
  const level = levelLabel(entry);
  return (
    <>
      {name}
      {level && <span className="location-record-level muted"> · {level}</span>}
    </>
  );
}

const HINTS: Record<RecordsKind, string> = {
  course:
    "Лучшее время площадки за всю её историю (в рамках вашего пола). Для площадок, " +
    "живших в нескольких системах, уровней два: рекорд системы и глобальный — сквозь " +
    "все системы; глобальный включает в себя рекорд своей системы. Утерянные рекорды " +
    "показаны серым — с датой и автором перебившего результата.",
  age_group:
    "Лучшее время площадки в вашей возрастной группе. Считается только по 5 вёрст — " +
    "единственной системе, публикующей возрастную категорию в протоколе. Утерянные " +
    "рекорды показаны серым — с датой и автором перебившего результата.",
};

const TITLES: Record<RecordsKind, string> = {
  course: "Рекорды локаций",
  age_group: "Рекорды в возрастных группах",
};

export function LocationRecordsModal({ open, onClose, kind, block }: LocationRecordsModalProps) {
  const entries = block?.entries ?? [];
  const hasLost = entries.some((entry) => !entry.is_current);

  return (
    <DetailModal open={open} title={TITLES[kind]} onClose={onClose}>
      {entries.length === 0 && <p className="muted unique-locations-empty">Рекордов пока нет.</p>}
      {entries.length > 0 && (
        <>
          <p className="muted personal-records-hint">{HINTS[kind]}</p>
          <div className="unique-locations-table-wrap">
            <table className="data-table unique-locations-table location-records-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Локация</th>
                  {kind === "age_group" && <th>Группа</th>}
                  <th>Система</th>
                  <th>Результат</th>
                  {hasLost && <th>Перебит</th>}
                </tr>
              </thead>
              <tbody>
                {entries.map((entry, index) => (
                  <tr
                    key={`${entry.location_name}-${entry.level ?? ""}-${entry.age_group ?? ""}-${index}`}
                    className={entry.is_current ? undefined : "location-record-lost"}
                  >
                    <td className="col-date">{formatDate(entry.event_date)}</td>
                    <td className="col-location">{locationCell(entry)}</td>
                    {kind === "age_group" && <td>{entry.age_group}</td>}
                    <td className="col-platform">
                      <PlatformBadge code={entry.platform_code} />
                    </td>
                    <td className="col-result">{entry.finish_time_display}</td>
                    {hasLost && (
                      <td className="col-beaten">
                        {entry.is_current ? (
                          <span className="muted">держится</span>
                        ) : (
                          <>
                            {entry.beaten_date ? formatDate(entry.beaten_date) : "—"}
                            {entry.beaten_by && (
                              <span className="location-record-beaten-by">
                                {" "}
                                — {entry.beaten_by}
                                {entry.beaten_time_display ? ` (${entry.beaten_time_display})` : ""}
                              </span>
                            )}
                          </>
                        )}
                      </td>
                    )}
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
