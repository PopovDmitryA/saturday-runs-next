import { PORTAL_LOGIN_HREF } from "../lib/portalRoutes";

/**
 * Продающая карточка «войдите и получите X» для публичных страниц (рейтинги,
 * локации, чужой профиль). Градиентная панель с крупной пиктограммой — должна
 * выделяться на фоне обычных карточек, это главный CTA страницы для анонима.
 */
export function PromoLoginCard({
  icon,
  title,
  text,
  cta = "Войти",
  href = PORTAL_LOGIN_HREF,
}: {
  icon: string;
  title: string;
  text: string;
  cta?: string;
  href?: string;
}) {
  return (
    <section className="promo-login-card" aria-label={title}>
      <span className="promo-login-card-glow" aria-hidden="true" />
      <span className="promo-login-card-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="promo-login-card-text">
        <strong className="promo-login-card-title">{title}</strong>
        <p>{text}</p>
      </div>
      <a className="promo-login-card-btn" href={href}>
        {cta}
      </a>
      <span className="promo-login-card-watermark" aria-hidden="true">
        {icon}
      </span>
    </section>
  );
}
