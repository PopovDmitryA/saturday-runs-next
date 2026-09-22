// Импорт треков участников: архив, файлы или ссылки → предпросмотр → кабинет.
// Треки до подтверждения лежат со статусом preview и участнику не видны.

import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "./AdminShell";
import { AdminSubnav } from "./AdminSubnav";
import { RequireAdmin } from "../../components/RequireAdmin";
import {
  applyTrackImport,
  createTrackImport,
  discardTrackImport,
  listAdminUsers,
  listTrackImports,
  processTrackImport,
  type AdminUserListItem,
  type TrackImportBatch,
  type TrackImportBatchDetail,
  type TrackImportItem,
} from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import "./adminTrackImports.css";

const STATUS_LABELS: Record<string, string> = {
  collecting: "разбираем файлы",
  previewing: "ждёт подтверждения",
  applied: "загружено в кабинет",
  discarded: "отменено",
  error: "ошибка",
};

function formatDistance(meters: number | null): string {
  return meters == null ? "—" : `${(meters / 1000).toFixed(2).replace(".", ",")} км`;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) {
    return "—";
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

function formatStart(value: string | null): string {
  return value ? formatDateTime(value) : "—";
}

function PreviewRow({ item }: { item: TrackImportItem }) {
  return (
    <tr className={item.matched_run ? (item.is_course_eligible ? "" : "excluded") : "no-match"}>
      <td>{formatStart(item.started_at)}</td>
      <td>
        {item.matched_run ? (
          <>
            <b>{item.location_name ?? "локация не названа"}</b>
            {item.event_date && <span className="dim"> · старт {item.event_date}</span>}
          </>
        ) : (
          <span className="dim">пробежка в протоколе не найдена</span>
        )}
      </td>
      <td className="num">{formatDistance(item.distance_m)}</td>
      <td className="num">{formatDuration(item.duration_sec)}</td>
      <td className="num">{item.elevation_gain_m == null ? "—" : `${Math.round(item.elevation_gain_m)} м`}</td>
      <td className="center">
        {item.has_elevation_profile ? (
          <span className="badge-ok" title="Высоты по точкам есть — профиль рельефа построится">
            есть
          </span>
        ) : (
          <span className="badge-warn" title="Высот по точкам нет: профиля рельефа не будет">
            нет
          </span>
        )}
      </td>
      <td className="center">{item.quality_class ?? "—"}</td>
      <td className="center">
        {item.is_course_eligible ? (
          <span className="badge-ok" title="Трек идёт в измерения трассы">
            да
          </span>
        ) : (
          <span className="badge-warn" title={item.exclusion_note ?? ""}>
            нет
          </span>
        )}
      </td>
      <td className="dim">{item.device_name ?? "—"}</td>
      <td className="num">
        {item.protocol_delta_sec == null ? "—" : `${item.protocol_delta_sec > 0 ? "+" : ""}${item.protocol_delta_sec} с`}
      </td>
    </tr>
  );
}

function GarminHowTo() {
  const [open, setOpen] = useState(false);
  return (
    <section className="track-import-howto">
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen((value) => !value)}>
        {open ? "Свернуть" : "Как попросить выгрузку из Garmin или COROS (с рельефом)"}
      </button>
      {open && (
        <div className="track-import-howto-body">
          <p>
            <b>Важно:</b> профиль рельефа строится только из файлов, где есть высоты по точкам. Подходят{" "}
            <b>оригинальный FIT</b> и <b>GPX из Garmin Connect</b> — в обоих лежат барометрические высоты
            часов. Ссылка на активность рельеф <b>не</b> содержит: из неё берутся только геометрия и общие
            цифры.
          </p>

          <h4>Garmin, вариант 1. Весь архив за все годы — лучший</h4>
          <ol>
            <li>
              Зайти на <code>garmin.com/account</code> под своей учётной записью (это отдельный сайт от
              Garmin Connect).
            </li>
            <li>
              Открыть раздел <b>«Управление данными» → «Экспорт данных»</b> (Export Your Data) и подтвердить
              запрос.
            </li>
            <li>
              Дождаться письма со ссылкой на архив. Обычно приходит за несколько часов, Garmin оставляет себе
              до 30 дней.
            </li>
            <li>
              Прислать zip целиком — распаковывать не нужно. Внутри лежат оригинальные FIT всех тренировок, я
              заберу из них только субботние пробежки.
            </li>
          </ol>
          <p className="dim">
            Один такой архив закрывает все локации человека сразу — это самый быстрый способ пополнить базу.
          </p>

          <h4>Garmin, вариант 2. Отдельные пробежки из Garmin Connect (веб)</h4>
          <ol>
            <li>
              Открыть <code>connect.garmin.com</code> на компьютере и зайти в нужную активность.
            </li>
            <li>
              Нажать шестерёнку в правом верхнем углу активности.
            </li>
            <li>
              Выбрать <b>«Экспортировать файл»</b> — скачается zip с оригинальным FIT. Это самый полный
              вариант: высоты, пульс, каденс, точность GPS.
            </li>
            <li>
              Если FIT почему-то не выходит — подойдёт <b>«Экспорт в GPX»</b> из того же меню: высоты в нём
              тоже есть.
            </li>
          </ol>
          <p className="dim">
            Пункты «Экспорт в TCX» и «Экспорт в Google Earth» брать не нужно: данных в них меньше.
          </p>

          <h4>Если у человека COROS</h4>
          <p>
            Подходит так же: в выгрузке COROS есть высоты, а значит и профиль рельефа. Форматы FIT и TCX
            наш разбор понимает.
          </p>
          <ol>
            <li>
              Открыть <code>t.coros.com</code> (COROS Training Hub) на компьютере — в мобильном приложении
              массовой выгрузки нет.
            </li>
            <li>
              Перейти на вкладку <b>Activity List</b> и нажать <b>Export Data</b> справа.
            </li>
            <li>
              Выбрать формат <b>FIT</b> (или TCX, если FIT недоступен) и указать почту — архив придёт
              письмом.
            </li>
            <li>Прислать архив целиком, как и в случае с Garmin.</li>
          </ol>
          <p className="dim">
            Одну пробежку можно выгрузить и с телефона: приложение COROS → «Профиль» → «Активности» →
            нужная активность → значок «Поделиться» справа сверху → «Экспорт».
          </p>

          <h4>Чего просить не надо</h4>
          <ul>
            <li>
              <b>Пароль от Garmin</b> — никогда. Человек выгружает файлы сам и присылает их.
            </li>
            <li>
              <b>Ссылку на активность</b> — только если файл получить не выходит: рельефа в ней нет.
            </li>
          </ul>
        </div>
      )}
    </section>
  );
}

function AdminTrackImportsContent() {
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<AdminUserListItem[]>([]);
  const [targetUser, setTargetUser] = useState<AdminUserListItem | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [links, setLinks] = useState("");
  const [batch, setBatch] = useState<TrackImportBatchDetail | null>(null);
  const [recent, setRecent] = useState<TrackImportBatch[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reloadRecent = useCallback(() => {
    listTrackImports(20)
      .then((response) => setRecent(response.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    reloadRecent();
  }, [reloadRecent]);

  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setUsers([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      listAdminUsers(term, 10)
        .then((response) => {
          if (!cancelled) {
            setUsers(response.items);
          }
        })
        .catch(() => undefined);
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  // Разбор идёт порциями: длинный архив не должен упираться в таймаут.
  const runProcessing = useCallback(async (started: TrackImportBatchDetail) => {
    let current = started;
    while (current.pending_count > 0) {
      current = await processTrackImport(current.id);
      setBatch(current);
    }
    return current;
  }, []);

  const handleUpload = async () => {
    if (!targetUser) {
      setError("Сначала выберите, чьи это пробежки");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createTrackImport(targetUser.id, files, links);
      setBatch(created);
      await runProcessing(created);
      reloadRecent();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleApply = async () => {
    if (!batch) {
      return;
    }
    setBusy(true);
    try {
      setBatch(await applyTrackImport(batch.id));
      setFiles([]);
      setLinks("");
      reloadRecent();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleDiscard = async () => {
    if (!batch) {
      return;
    }
    setBusy(true);
    try {
      setBatch(await discardTrackImport(batch.id));
      reloadRecent();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const withProfile = batch?.items.filter((item) => item.has_elevation_profile).length ?? 0;
  const matched = batch?.items.filter((item) => item.matched_run).length ?? 0;
  const eligible = batch?.items.filter((item) => item.is_course_eligible).length ?? 0;

  return (
    <div className="track-import-page">
      <p className="track-import-intro">
        Загрузка чужих треков: архив выгрузки, отдельные файлы (GPX, TCX, FIT) или ссылки на активности
        Garmin. Треки сначала показываются здесь и попадают в кабинет участника только после подтверждения.
      </p>

      <GarminHowTo />

      <section className="track-import-form">
        <label className="track-import-field">
          <span>Чьи это пробежки</span>
          {targetUser ? (
            <div className="track-import-chosen">
              <b>{targetUser.display_name ?? "без имени"}</b>
              <span className="dim">
                №{targetUser.serial_id ?? "—"} · пробежек {targetUser.total_runs ?? 0}
                {targetUser.telegram_username ? ` · @${targetUser.telegram_username}` : ""}
              </span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTargetUser(null)}>
                сменить
              </button>
            </div>
          ) : (
            <>
              <input
                type="search"
                placeholder="имя, ник или почта участника"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {users.length > 0 && (
                <ul className="track-import-users">
                  {users.map((user) => (
                    <li key={user.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setTargetUser(user);
                          setQuery("");
                          setUsers([]);
                        }}
                      >
                        <b>{user.display_name ?? "без имени"}</b>
                        <span className="dim">
                          №{user.serial_id ?? "—"} · пробежек {user.total_runs ?? 0}
                          {user.telegram_username ? ` · @${user.telegram_username}` : ""}
                          {user.home_location?.name ? ` · ${user.home_location.name}` : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </label>

        <label className="track-import-field">
          <span>Файлы и архивы</span>
          <input
            type="file"
            multiple
            accept=".zip,.gpx,.tcx,.fit"
            onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
          />
          {files.length > 0 && <span className="dim">выбрано файлов: {files.length}</span>}
        </label>

        <label className="track-import-field">
          <span>Ссылки на активности Garmin, по одной в строке</span>
          <textarea
            rows={3}
            placeholder="https://connect.garmin.com/modern/activity/24243154472"
            value={links}
            onChange={(event) => setLinks(event.target.value)}
          />
        </label>

        <div className="track-import-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !targetUser || (files.length === 0 && !links.trim())}
            onClick={() => void handleUpload()}
          >
            {busy ? "Разбираем…" : "Загрузить и посмотреть"}
          </button>
        </div>
        {error && <p className="form-error">{error}</p>}
      </section>

      {batch && (
        <section className="track-import-result">
          <h3>
            Загрузка для {batch.target_user_name ?? "участника"} · {STATUS_LABELS[batch.status] ?? batch.status}
          </h3>
          <p className="track-import-counters">
            Разобрано {batch.processed_count} из {batch.total_count} · распознано треков{" "}
            <b>{batch.imported_count}</b> · пропущено {batch.skipped_count}
            {batch.pending_count > 0 && <> · в очереди {batch.pending_count}</>}
          </p>
          {batch.items.length > 0 && (
            <p className="track-import-counters">
              С профилем рельефа: <b>{withProfile}</b> из {batch.items.length} · привязано к пробежкам из
              протокола: <b>{matched}</b> · годятся для измерения трассы: <b>{eligible}</b>
            </p>
          )}
          {batch.items.length > 0 && matched === 0 && (
            // Тёзки в базе — обычное дело, и промах по участнику выглядит
            // именно так: треки разобрались, а пробежек под них нет.
            <p className="track-import-warning">
              Ни один трек не совпал с пробежками этого участника. Скорее всего, выбран не тот человек —
              проверьте номер профиля и число пробежек, прежде чем подтверждать.
            </p>
          )}

          {batch.items.length > 0 && (
            <div className="track-import-table-wrap">
              <table className="track-import-table">
                <thead>
                  <tr>
                    <th>начало</th>
                    <th>куда ляжет</th>
                    <th className="num">длина</th>
                    <th className="num">время</th>
                    <th className="num">набор</th>
                    <th className="center">рельеф</th>
                    <th className="center">класс</th>
                    <th className="center">в трассу</th>
                    <th>часы</th>
                    <th className="num">± протокол</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.items.map((item) => (
                    <PreviewRow key={item.track_id} item={item} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {batch.items.some((item) => !item.is_course_eligible && item.matched_run) && (
            <details className="track-import-problems">
              <summary>
                Не пойдут в измерения трассы:{" "}
                {batch.items.filter((item) => !item.is_course_eligible && item.matched_run).length}
              </summary>
              <ul>
                {batch.items
                  .filter((item) => !item.is_course_eligible && item.matched_run)
                  .map((item) => (
                    <li key={`ex-${item.track_id}`}>
                      {item.location_name ?? "локация"} · {item.event_date ?? ""} — {item.exclusion_note}
                    </li>
                  ))}
              </ul>
            </details>
          )}

          {batch.problems.length > 0 && (
            <details className="track-import-problems">
              <summary>Не разобрано: {batch.problems.length}</summary>
              <ul>
                {batch.problems.slice(0, 200).map((problem, index) => (
                  <li key={`${problem.name}-${index}`}>
                    <code>{problem.name}</code> — {problem.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {(batch.status === "previewing" || batch.status === "collecting") && (
            <div className="track-import-actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || batch.pending_count > 0 || batch.imported_count === 0}
                onClick={() => void handleApply()}
              >
                Подтвердить и записать в кабинет
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void handleDiscard()}>
                Отменить загрузку
              </button>
            </div>
          )}
        </section>
      )}

      {recent.length > 0 && (
        <section className="track-import-recent">
          <h3>Последние загрузки</h3>
          <table className="track-import-table">
            <thead>
              <tr>
                <th>когда</th>
                <th>кому</th>
                <th className="num">треков</th>
                <th className="num">пропущено</th>
                <th>статус</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((item) => (
                <tr key={item.id}>
                  <td>{formatDateTime(item.created_at)}</td>
                  <td>{item.target_user_name ?? "—"}</td>
                  <td className="num">{item.imported_count}</td>
                  <td className="num">{item.skipped_count}</td>
                  <td>{STATUS_LABELS[item.status] ?? item.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export function AdminTrackImportsPage() {
  return (
    <RequireAdmin>
      <AdminShell title="Треки участников">
        <AdminSubnav activePath="/admin/track-imports" />
        <AdminTrackImportsContent />
      </AdminShell>
    </RequireAdmin>
  );
}
