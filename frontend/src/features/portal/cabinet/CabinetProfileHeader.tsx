import type { DashboardResponse, User } from "../../../lib/api";
import { pluralFormRu } from "../../../lib/format";
import { PORTAL_CABINET_SHARE_HREF } from "../../../lib/portalRoutes";
import { userLabel } from "./PortalCabinetShell";

/** Клубы пробежек — те же ступени, что и в вехах «Моей истории». */
const RUN_CLUBS = [1000, 500, 250, 100, 50, 25, 10];

function earnedClub(totalRuns: number): number | null {
  return RUN_CLUBS.find((club) => totalRuns >= club) ?? null;
}

function initials(name: string): string {
  const clean = name.replace(/^@/, "").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  return parts.length >= 2
    ? (parts[0][0] + parts[1][0]).toUpperCase()
    : clean.slice(0, 2).toUpperCase();
}

/**
 * Шапка кабинета как профиль человека, а не приборная панель: кто ты,
 * чем отмечен, сколько систем собрано. Приветствие «Добрый вечер!» здесь
 * не нужно — оно не сообщает ничего о самом участнике.
 */
export function CabinetProfileHeader({
  data,
  user,
}: {
  data: DashboardResponse;
  user: User;
}) {
  const stats = data.stats;
  const analytics = stats?.analytics;
  const name = userLabel(user);
  const totalRuns = stats?.total_runs ?? 0;
  const club = earnedClub(totalRuns);
  const locations = analytics?.unique_run_locations ?? 0;
  const streak = analytics?.saturday_streak_current ?? 0;
  const platforms = Object.values(stats?.by_platform ?? {}).filter(Boolean).length;
  const firstRunYear = analytics?.first_run_date?.slice(0, 4);

  const chips: string[] = [];
  if (club) {
    chips.push(`Клуб ${club}`);
  }
  if (locations > 0) {
    chips.push(`${locations} ${pluralFormRu(locations, ["локация", "локации", "локаций"])}`);
  }
  if (streak > 0) {
    chips.push(`${streak} ${pluralFormRu(streak, ["суббота", "субботы", "суббот"])} подряд`);
  }
  if (platforms > 0) {
    chips.push(`${platforms} ${pluralFormRu(platforms, ["система", "системы", "систем"])}`);
  }

  return (
    <header className="cab-profile">
      <div className="cab-profile-avatar" aria-hidden="true">
        {user.avatar_url ? <img src={user.avatar_url} alt="" /> : <span>{initials(name)}</span>}
      </div>
      <div className="cab-profile-body">
        <h1 className="cab-profile-name">{name}</h1>
        {firstRunYear && (
          <p className="cab-profile-since">Бегает по субботам с {firstRunYear} года</p>
        )}
        {chips.length > 0 && (
          <ul className="cab-profile-chips">
            {chips.map((chip) => (
              <li key={chip}>{chip}</li>
            ))}
          </ul>
        )}
      </div>
      <a className="btn secondary cab-profile-share" href={PORTAL_CABINET_SHARE_HREF}>
        Поделиться
      </a>
    </header>
  );
}
