import { useCallback, useEffect, useState } from "react";
import { DetailModal } from "../../components/DetailModal";
import { ElevationProfile } from "./ElevationProfile";
import { RunTrackMap } from "./RunTrackMap";
import {
  confirmRunTrack,
  deleteRunTrack,
  getRunTrack,
  importRunTrackLink,
  uploadRunTrack,
  type RunItem,
  type RunTrackDetail,
  type RunTrackSplit,
} from "../../lib/api";
import { formatDateLong } from "../../lib/format";

type RunTrackModalProps = {
  run: RunItem;
  onClose: () => void;
  // Трек появился или удалён — обновляем значок в таблице.
  onChanged: (trackId: string | null) => void;
};

function paceLabel(seconds: number, meters: number): string {
  if (meters <= 0) {
    return "—";
  }
  const perKm = (seconds / meters) * 1000;
  const minutes = Math.floor(perKm / 60);
  return `${minutes}:${String(Math.round(perKm % 60)).padStart(2, "0")}`;
}

function timeLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

function splitLabel(split: RunTrackSplit): string {
  return split.km != null ? `${split.km}-й км` : `последние ${split.meters} м`;
}

/** Словесная оценка записи: человеку важно не число, а годится ли его трек. */
function qualityText(track: RunTrackDetail): string {
  if (track.quality_class === "A") {
    return "Запись посекундная, без пропусков.";
  }
  if (track.quality_class === "B") {
    return "Запись с интервалом в несколько секунд: для разбора хватает, для замера трассы — нет.";
  }
  return "Запись редкая или с пропусками.";
}

export function RunTrackModal({ run, onClose, onChanged }: RunTrackModalProps) {
  const [track, setTrack] = useState<RunTrackDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(run.track_id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  // Что именно сейчас разбирается: без этого человек выбирал файл и сидел
  // перед неизменившимся окном, не понимая, идёт ли что-нибудь.
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    if (!run.track_id) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    getRunTrack(run.track_id)
      .then((detail) => {
        if (!cancelled) {
          setTrack(detail);
        }
      })
      .catch((cause: Error) => {
        if (!cancelled) {
          setError(cause.message);
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
  }, [run.track_id]);

  const accept = useCallback(
    async (action: Promise<RunTrackDetail>) => {
      setBusy(true);
      setError(null);
      try {
        const detail = await action;
        setTrack(detail);
        // Черновик в таблицу пробежек не попадает: значок появится только
        // после «Сохранить».
        if (detail.status !== "preview") {
          onChanged(detail.id);
        }
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setBusy(false);
        setPending(null);
      }
    },
    [onChanged],
  );

  const handleFile = (file: File | undefined) => {
    if (file) {
      setPending(file.name);
      void accept(uploadRunTrack(file));
    }
  };

  const handleLink = () => {
    const value = url.trim();
    if (value) {
      setPending("ссылку на активность");
      void accept(importRunTrackLink(value));
    }
  };

  const handleConfirm = async () => {
    if (!track) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await confirmRunTrack(track.id);
      setTrack(saved);
      onChanged(saved.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!track) {
      return;
    }
    setBusy(true);
    try {
      await deleteRunTrack(track.id);
      setTrack(null);
      onChanged(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isDraft = track?.status === "preview";
  const title = `Трек пробежки · ${formatDateLong(run.event_date)}`;

  return (
    <DetailModal open title={title} onClose={onClose}>
      {loading && <p className="muted">Загружаем трек…</p>}
      {/* Ошибку показываем рядом с самим действием: наверху модалки её
          проглядывали, а при загрузке взгляд остаётся на кнопке выбора. */}
      {error && track && <p className="form-error">{error}</p>}

      {!loading && !track && (
        <div className="run-track-upload">
          <p>
            Приложите трек этой пробежки — покажем темп по километрам, профиль трассы и сверим время с
            протоколом. Подойдёт файл с часов (GPX, TCX или оригинальный FIT) или ссылка на активность
            Garmin Connect.
          </p>
          <label className="run-track-file">
            <input
              type="file"
              accept=".gpx,.tcx,.fit,application/gpx+xml,application/octet-stream"
              disabled={busy}
              onChange={(event) => handleFile(event.target.files?.[0])}
            />
            <span>{busy ? "Разбираем…" : "Выбрать файл"}</span>
          </label>

          {busy && pending && (
            <p className="run-track-progress">
              <span className="run-track-spinner" aria-hidden />
              Разбираем {pending} — это занимает несколько секунд.
            </p>
          )}

          {error && (
            <div className="form-error">
              <b>Трек не загрузился.</b> {error}
              <span className="run-track-error-hint">
                Выберите другой файл или приложите ссылку на активность — окно можно не закрывать.
              </span>
            </div>
          )}
          <div className="run-track-link">
            <input
              type="url"
              placeholder="https://connect.garmin.com/modern/activity/…"
              value={url}
              disabled={busy}
              onChange={(event) => setUrl(event.target.value)}
            />
            <button type="button" className="btn" disabled={busy || !url.trim()} onClick={handleLink}>
              Импортировать
            </button>
          </div>
          <p className="muted small">
            Из ссылки берём геометрию и цифры прибора, из файла — ещё и высоты по точкам, а значит профиль
            трассы. Активность должна
            быть открыта настройками приватности. Храним только окрестность старта: дорога от дома в базу не
            попадает.
          </p>
        </div>
      )}

      {track && (
        <div className="run-track-detail">
          <div className="run-track-facts">
            <div>
              <b>{track.distance_m ? (track.distance_m / 1000).toFixed(2).replace(".", ",") : "—"} км</b>
              <span>по треку</span>
            </div>
            <div>
              <b>{track.duration_sec ? timeLabel(track.duration_sec) : "—"}</b>
              <span>по часам</span>
            </div>
            <div>
              <b>
                {track.duration_sec && track.distance_m
                  ? paceLabel(track.duration_sec, track.distance_m)
                  : "—"}
              </b>
              <span>средний темп</span>
            </div>
            <div>
              <b>{track.elevation_gain_m != null ? `${Math.round(track.elevation_gain_m)} м` : "—"}</b>
              <span>набор высоты</span>
            </div>
          </div>

          <RunTrackMap points={track.points} />

          <ElevationProfile metrics={track.metrics} deviceGainM={track.elevation_gain_m} />

          {track.metrics.splits && track.metrics.splits.length > 0 && (
            <table className="run-track-splits">
              <tbody>
                {track.metrics.splits.map((split, index) => (
                  <tr key={`${split.km ?? "tail"}-${index}`}>
                    <td>{splitLabel(split)}</td>
                    <td className="num">{timeLabel(split.seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <ul className="run-track-notes">
            {track.metrics.lap_count != null && track.metrics.lap_count > 1 && (
              <li>
                Трасса в {track.metrics.lap_count} круга по{" "}
                {((track.metrics.lap_length_m ?? 0) / 1000).toFixed(2).replace(".", ",")} км.
              </li>
            )}
            {track.metrics.turn_sum_deg != null && (
              <li>
                Суммарно трасса поворачивает на {track.metrics.turn_sum_deg}°
                {track.metrics.u_turn_count ? `, включая ${track.metrics.u_turn_count} разворотов` : ""}.
                Самая длинная прямая — {track.metrics.longest_straight_m} м.
              </li>
            )}
            {track.protocol_delta_sec != null && (
              <li>
                Время по треку{" "}
                {track.protocol_delta_sec === 0
                  ? "совпадает с протоколом"
                  : `отличается от протокола на ${Math.abs(track.protocol_delta_sec)} с`}
                .
              </li>
            )}
            {track.metrics.uphill_share != null && (
              <li>
                В подъём {Math.round(track.metrics.uphill_share * 100)}% дистанции, под уклон{" "}
                {Math.round((track.metrics.downhill_share ?? 0) * 100)}%, остальное — плоско.
              </li>
            )}
            {track.device_distance_m != null && track.distance_m != null && (
              <li>
                Прибор показал {(track.device_distance_m / 1000).toFixed(2).replace(".", ",")} км, наш замер —{" "}
                {(track.distance_m / 1000).toFixed(2).replace(".", ",")} км.
              </li>
            )}
            <li>
              {qualityText(track)}
              {track.device_name ? ` Устройство: ${track.device_name}.` : ""}
            </li>
            {!track.is_course_eligible && track.exclusion_note && (
              <li>В измерения трассы не идёт: {track.exclusion_note.toLowerCase()}.</li>
            )}
          </ul>

          {isDraft ? (
            <div className="run-track-actions run-track-actions-draft">
              <p className="run-track-draft-note">
                Трек пока никуда не записан — это разбор приложенного файла. Сохраните, чтобы он появился
                в вашем профиле и пошёл в измерения трассы.
              </p>
              <div className="run-track-actions-row">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => void handleConfirm()}
                >
                  Сохранить трек
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => void handleDelete()}>
                  Отменить
                </button>
              </div>
            </div>
          ) : (
            <div className="run-track-actions">
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void handleDelete()}
              >
                Удалить трек
              </button>
            </div>
          )}
        </div>
      )}
    </DetailModal>
  );
}
