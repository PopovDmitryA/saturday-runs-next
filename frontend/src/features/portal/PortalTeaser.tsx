import { useState } from "react";
import { trackCtaClick } from "../../lib/abTest";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { fetchPortalTeaser, PortalHomeError, type PortalTeaser } from "./portalTypes";
import { COUNT_FORMS, formatInt, pluralFormRu } from "../../lib/format";
import { rememberTeaserClaim } from "./teaserClaim";

/**
 * Тизер главной: посетитель выбирает свою систему, вводит ID — и
 * видит предпросмотр СВОЕЙ карточки на реальных данных из нашей БД. Полная
 * статистика — за регистрацией; тизер намеренно показывает только затравку.
 *
 * Кнопка под предпросмотром не просто ведёт на вход: введённый ID переживает
 * редирект к провайдеру и после регистрации сам превращается в привязку
 * профиля (см. teaserClaim). Иначе человек, только что посмотревший на свои
 * цифры, попадал бы в пустой кабинет и вводил тот же ID заново.
 */

const TEASER_PLATFORMS: { code: string; title: string; hint: string }[] = [
  { code: "five_verst", title: "5 вёрст", hint: "ID участника 5 вёрст, например 147455" },
  { code: "s95", title: "S95", hint: "ID участника S95, например 11071" },
  { code: "parkrun", title: "parkrun", hint: "parkrun ID, например A331" },
  { code: "runpark", title: "RunPark", hint: "ID участника RunPark, например 20155" },
];

function formatDateShort(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function PortalTeaserCard() {
  const [platform, setPlatform] = useState(TEASER_PLATFORMS[0]);
  const [athleteId, setAthleteId] = useState("");
  const [teaser, setTeaser] = useState<PortalTeaser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const lookup = () => {
    const cleaned = athleteId.trim();
    if (!cleaned || loading) {
      return;
    }
    setLoading(true);
    setError(null);
    fetchPortalTeaser(platform.code, cleaned)
      .then((result) => setTeaser(result))
      .catch((err) => {
        setTeaser(null);
        const status = err instanceof PortalHomeError ? err.status : null;
        if (status === 404) {
          setError("Не нашли такой ID — проверьте номер и систему.");
        } else if (status === 429) {
          setError("Слишком много попыток — подождите минуту.");
        } else {
          setError("Не получилось загрузить, попробуйте ещё раз.");
        }
      })
      .finally(() => setLoading(false));
  };

  return (
    <div className="portal-teaser" aria-label="Предпросмотр вашей статистики">
      <div className="portal-teaser-tabs" role="tablist" aria-label="Беговая система">
        {TEASER_PLATFORMS.map((item) => (
          <button
            key={item.code}
            type="button"
            role="tab"
            aria-selected={item.code === platform.code}
            className={item.code === platform.code ? "active" : ""}
            onClick={() => {
              setPlatform(item);
              setTeaser(null);
              setError(null);
            }}
          >
            {item.title}
          </button>
        ))}
      </div>

      <div className="portal-teaser-form">
        <input
          type="text"
          inputMode="numeric"
          placeholder={platform.hint}
          value={athleteId}
          onChange={(event) => setAthleteId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              lookup();
            }
          }}
          aria-label={`ID в системе ${platform.title}`}
        />
        <button type="button" className="btn primary" onClick={lookup} disabled={loading}>
          {loading ? "Ищем…" : "Показать"}
        </button>
      </div>

      {error && <p className="portal-teaser-error">{error}</p>}

      {teaser && (
        <div className="portal-teaser-result">
          <div className="portal-teaser-name">
            <b>{teaser.display_name ?? "Участник"}</b>
            {teaser.first_event_date && <span>бегает с {formatDateShort(teaser.first_event_date)}</span>}
          </div>
          <div className="portal-teaser-grid">
            <div className="portal-teaser-tile">
              <b className="num">{formatInt(teaser.finishes)}</b>
              <span>{pluralFormRu(teaser.finishes, COUNT_FORMS.finishes)}</span>
            </div>
            {teaser.best_time_display && (
              <div className="portal-teaser-tile">
                <b className="num">{teaser.best_time_display}</b>
                <span>лучшее время</span>
              </div>
            )}
            <div className="portal-teaser-tile">
              <b className="num">{formatInt(teaser.locations)}</b>
              <span>{pluralFormRu(teaser.locations, COUNT_FORMS.locations)}</span>
            </div>
          </div>
          <p className="portal-teaser-more">
            А ещё — рекорды по годам, серии суббот, карта визитов, встречи и вехи. Всё это уже
            посчитано и ждёт в кабинете.
          </p>
          <a
            className="btn primary"
            href={PORTAL_LOGIN_HREF}
            onClick={() => {
              rememberTeaserClaim(platform.code, athleteId.trim(), platform.title);
              trackCtaClick("teaser");
            }}
          >
            Сохранить в кабинет
          </a>
          <p className="portal-teaser-note">
            Войдите — и профиль {platform.title} привяжется сам, вводить ID заново не придётся.
          </p>
        </div>
      )}
    </div>
  );
}
