import { useEffect, useMemo, useState } from "react";
import { DetailModal } from "../../components/DetailModal";
import { platformCodeLabel } from "../../lib/format";
import { getStartNumbersPlan, type StartNumberPlan } from "../../lib/api";

/** «01.08» — в таблице год не нужен, все три окна внутри трёх недель. */
function shortDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return day && month ? `${day}.${month}` : iso;
}

// Колонки считаем в забегах локации, а не в календарных неделях: E — ближайший
// старт локации, E+1 — следующий за ним. Даты у каждой записи свои, поэтому
// границы окна в шапке не пишем — они только сбивали.
function columnTitle(index: number): string {
  return index === 0 ? "Ближайший забег (E)" : `E+${index}`;
}

/** Короткая подпись для мобильной раскладки, где шапки таблицы нет. */
function columnShortTitle(index: number): string {
  return index === 0 ? "E" : `E+${index}`;
}

export function StartNumbersPlanModal({
  open,
  code,
  challengeTitle,
  platform = null,
  onClose,
}: {
  open: boolean;
  code: string;
  challengeTitle: string;
  // Фильтр систем со страницы достижений: под «5 вёрст» таблица не должна
  // предлагать старты s95 и runpark — челлендж в этом режиме считается только
  // по выбранной системе.
  platform?: string | null;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<StartNumberPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [onlyWithStarts, setOnlyWithStarts] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getStartNumbersPlan(code, platform)
      .then((data) => {
        if (!cancelled) {
          setPlan(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Не удалось загрузить план. Попробуй ещё раз.");
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
  }, [open, code, platform]);

  const rows = useMemo(() => {
    if (!plan) {
      return [];
    }
    return plan.rows.filter((row) => {
      if (onlyOpen && row.done) {
        return false;
      }
      if (onlyWithStarts && row.weeks.every((week) => week.length === 0)) {
        return false;
      }
      return true;
    });
  }, [plan, onlyOpen, onlyWithStarts]);

  const plannedCount = useMemo(
    () =>
      (plan?.rows ?? []).filter(
        (row) => !row.done && row.weeks.some((week) => week.length > 0),
      ).length,
    [plan],
  );

  return (
    <DetailModal open={open} title={`Планирование — ${challengeTitle}`} onClose={onClose}>
      {platform && (
        <p className="plan-summary">
          Показаны только старты системы <strong>{platformCodeLabel(platform)}</strong> — как и
          выбрано фильтром на странице достижений.
        </p>
      )}
      <p className="muted plan-intro">
        Прогноз строится по последнему известному старту каждой локации: номер и дата сдвигаются на
        неделю вперёд. Локация может пропустить субботу — тогда старт уедет на неделю позже.
      </p>

      {plan && (
        <p className="plan-summary">
          Незакрытых номеров, которые можно взять в ближайшие 3 недели: <strong>{plannedCount}</strong>
        </p>
      )}

      <div className="plan-filters">
        <label className="plan-filter">
          <input
            type="checkbox"
            checked={onlyOpen}
            onChange={(event) => setOnlyOpen(event.target.checked)}
          />
          Только незакрытые мной
        </label>
        <label className="plan-filter">
          <input
            type="checkbox"
            checked={onlyWithStarts}
            onChange={(event) => setOnlyWithStarts(event.target.checked)}
          />
          Только со стартами
        </label>
      </div>

      {loading && <p className="muted">Загружаю…</p>}
      {error && <p className="form-error">{error}</p>}

      {plan && !loading && (
        <div className="plan-table-wrap">
          <table className="plan-table">
            <thead>
              <tr>
                <th scope="col">№</th>
                {Array.from({ length: plan.week_count }, (_, index) => (
                  <th key={index} scope="col">
                    {columnTitle(index)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const empty = row.weeks.every((entries) => entries.length === 0);
                return (
                  <tr
                    key={row.number}
                    className={`${row.done ? "plan-row-done" : "plan-row-open"}${
                      empty ? " plan-row-empty" : ""
                    }`}
                  >
                    <th scope="row" className="plan-number">
                      <span className="plan-number-value">№{row.number}</span>
                      <span className="plan-number-state" aria-hidden="true">
                        {row.done ? "✓" : ""}
                      </span>
                      <span className="visually-hidden">{row.done ? "закрыт" : "не закрыт"}</span>
                      {/* Виден только в мобильной раскладке, где ячейки без стартов скрыты. */}
                      {empty && <span className="plan-number-note">нет стартов</span>}
                    </th>
                    {row.weeks.map((entries, index) => (
                      <td
                        key={index}
                        data-label={columnShortTitle(index)}
                        className={entries.length === 0 ? "plan-cell-empty" : undefined}
                      >
                        {entries.length === 0 ? (
                          <span className="plan-empty">—</span>
                        ) : (
                          <ul className="plan-entries">
                            {entries.map((entry) => (
                              <li key={`${entry.location_slug}-${entry.platform_code}-${entry.date}`}>
                                <a href={`/locations/${entry.location_slug}`}>{entry.location}</a>
                                <span className="plan-entry-meta">
                                  {shortDate(entry.date)} · {platformCodeLabel(entry.platform_code)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <p className="muted">Под выбранные фильтры ничего не попало.</p>}
        </div>
      )}
    </DetailModal>
  );
}
