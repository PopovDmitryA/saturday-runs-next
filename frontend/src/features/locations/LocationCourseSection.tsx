// Паспорт трассы локации: профиль рельефа и цифры по трекам участников.
// Пока фича закрыта, блок виден только админу (ручка отвечает 404 остальным).

import { useEffect, useState } from "react";
import { pluralizeRu } from "../../lib/format";
import { LocationCourseMap } from "./LocationCourseMap";
import {
  getLocationCourse,
  type CourseProfile,
  type LocationCourseResponse,
} from "../leaderboards/courseApi";
import "./locationCourse.css";

const WIDTH = 640;
const HEIGHT = 180;
const PADDING = { top: 14, right: 10, bottom: 24, left: 34 };
// Плоской трассе нужен минимальный размах шкалы, иначе график превращается
// в кардиограмму из шума.
const MIN_SPAN_M = 8;

function ProfileChart({ profile }: { profile: CourseProfile }) {
  const points = profile.elevation_profile;
  if (points.length < 2) {
    return null;
  }
  const maxDistance = points[points.length - 1][0] || 1;
  const heights = points.map(([, height]) => height);
  const low = Math.min(...heights);
  const high = Math.max(low + MIN_SPAN_M, Math.max(...heights));

  const x = (distance: number) =>
    PADDING.left + (distance / maxDistance) * (WIDTH - PADDING.left - PADDING.right);
  const y = (height: number) =>
    PADDING.top + (1 - (height - low) / (high - low)) * (HEIGHT - PADDING.top - PADDING.bottom);
  const line = points
    .map(([distance, height], index) => `${index === 0 ? "M" : "L"}${x(distance).toFixed(1)} ${y(height).toFixed(1)}`)
    .join("");
  const baseline = HEIGHT - PADDING.bottom;
  const area = `${line}L${x(maxDistance).toFixed(1)} ${baseline}L${x(0).toFixed(1)} ${baseline}Z`;

  const ticks: number[] = [];
  for (let km = 1; km * 1000 < maxDistance; km += 1) {
    ticks.push(km);
  }

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Профиль трассы по километражу">
      {ticks.map((km) => (
        <g key={km}>
          <line x1={x(km * 1000)} y1={PADDING.top} x2={x(km * 1000)} y2={baseline} className="grid" />
          <text x={x(km * 1000)} y={HEIGHT - 7} textAnchor="middle" className="axis">
            {km} км
          </text>
        </g>
      ))}
      <path d={area} className="area" />
      <path d={line} className="line" />
      <text x={PADDING.left - 5} y={y(high) + 4} textAnchor="end" className="axis">
        +{Math.round(high - low)} м
      </text>
      <text x={PADDING.left - 5} y={baseline} textAnchor="end" className="axis">
        0
      </text>
    </svg>
  );
}

function Facts({ profile }: { profile: CourseProfile }) {
  return (
    <div className="loc-course-facts">
      <div>
        <b>{profile.distance_m ? (profile.distance_m / 1000).toFixed(2).replace(".", ",") : "—"} км</b>
        <span>длина по трекам</span>
      </div>
      <div>
        <b>{profile.elevation_span_m != null ? `${profile.elevation_span_m} м` : "—"}</b>
        <span>перепад высот</span>
      </div>
      <div>
        <b>{profile.elevation_gain_m != null ? `${Math.round(profile.elevation_gain_m)} м` : "—"}</b>
        <span>набор за пробежку</span>
      </div>
      <div>
        <b>
          {profile.lap_count && profile.lap_count > 1
            ? `${profile.lap_count} круга`
            : "одна петля"}
        </b>
        <span>{profile.lap_count && profile.lap_count > 1 && profile.distance_m
          ? `по ${(profile.distance_m / profile.lap_count / 1000).toFixed(2).replace(".", ",")} км`
          : "без повторов"}</span>
      </div>
      <div>
        <b>{profile.turn_sum_deg != null ? `${Math.round(profile.turn_sum_deg)}°` : "—"}</b>
        <span>суммарный поворот</span>
      </div>
    </div>
  );
}

export function LocationCourseSection({ slug }: { slug: string }) {
  const [data, setData] = useState<LocationCourseResponse | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getLocationCourse(slug)
      .then((response) => {
        if (!cancelled) {
          setData(response);
        }
      })
      // 404 здесь штатный: фича закрыта либо локации нет.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (!data) {
    return null;
  }

  const profile = data.current;

  return (
    <section className="card loc-section">
      <div className="loc-course-head">
        <h2 className="section-title">Трасса по трекам участников</h2>
        {data.has_data && profile && (
          <span className="loc-course-source">
            по {pluralizeRu(profile.tracks_count, ["треку", "трекам", "трекам"])} от{" "}
            {pluralizeRu(profile.unique_user_count, ["участника", "участников", "участников"])}
          </span>
        )}
      </div>

      {!data.has_data || !profile ? (
        <p className="loc-course-empty">
          Треков этой локации пока нет — профиль трассы появится, когда участники их приложат.
        </p>
      ) : (
        <>
          <Facts profile={profile} />
          <LocationCourseMap geometry={profile.geometry as [number, number][]} />
          <p className="loc-course-caption">Профиль трассы: высота над низшей точкой по километражу</p>
          <ProfileChart profile={profile} />
          <ul className="loc-course-notes">
            {profile.climb_length_m != null && profile.climb_rise_m != null && (
              <li>
                Главный подъём: {Math.round(profile.climb_length_m)} м пути, за них трасса поднимается на{" "}
                {profile.climb_rise_m.toFixed(1).replace(".", ",")} м
                {profile.climb_grade_percent != null
                  ? ` (уклон ${String(profile.climb_grade_percent).replace(".", ",")}%)`
                  : ""}
                .
              </li>
            )}
            {profile.uphill_share != null && (
              <li>
                В подъём {Math.round(profile.uphill_share * 100)}% дистанции, под уклон{" "}
                {Math.round((profile.downhill_share ?? 0) * 100)}%, остальное — плоско.
              </li>
            )}
            {profile.longest_straight_m != null && (
              <li>Самый длинный прямой участок — {Math.round(profile.longest_straight_m)} м.</li>
            )}
            {profile.distance_min_m != null && profile.distance_max_m != null && profile.tracks_count > 1 && (
              <li>
                Замеры разных пробежек: от {(profile.distance_min_m / 1000).toFixed(2).replace(".", ",")} до{" "}
                {(profile.distance_max_m / 1000).toFixed(2).replace(".", ",")} км.
              </li>
            )}
          </ul>

          {data.history.length > 0 && (
            <div className="loc-course-history">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowHistory((v) => !v)}>
                {showHistory ? "Скрыть прежние трассы" : `Прежние трассы: ${data.history.length}`}
              </button>
              {showHistory && (
                <table className="loc-course-history-table">
                  <thead>
                    <tr>
                      <th>версия</th>
                      <th className="num">длина</th>
                      <th className="num">перепад</th>
                      <th className="num">поворот</th>
                      <th className="num">треков</th>
                      <th>когда бегали</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.history.map((item) => (
                      <tr key={item.course_version}>
                        <td>№{item.course_version}</td>
                        <td className="num">
                          {item.distance_m ? (item.distance_m / 1000).toFixed(2).replace(".", ",") : "—"}
                        </td>
                        <td className="num">{item.elevation_span_m ?? "—"}</td>
                        <td className="num">
                          {item.turn_sum_deg != null ? `${Math.round(item.turn_sum_deg)}°` : "—"}
                        </td>
                        <td className="num">{item.tracks_count}</td>
                        <td>
                          {item.first_track_at?.slice(0, 10) ?? "—"} — {item.last_track_at?.slice(0, 10) ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
