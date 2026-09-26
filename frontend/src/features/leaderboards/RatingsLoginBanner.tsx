import { PromoLoginCard } from "../../components/PromoLoginCard";
import { useOptionalUser } from "../../lib/useOptionalUser";

/**
 * Продающий баннер для анонима в разделе «Рейтинги»: раздел открыт без логина,
 * но свою строку и позицию видит только залогиненный. Залогиненному (и пока
 * сессия проверяется) не рендерится вовсе.
 *
 * На телефоне карточка ужата до одной строки «🏆 А где здесь вы? · Войти»
 * (.lb-promo-login в leaderboards.css): во весь рост она занимала 270px из
 * 740, и до таблицы гость не доставал без прокрутки (проверка 26.09.2026).
 */
export function RatingsLoginBanner() {
  const user = useOptionalUser();
  if (user !== null) {
    return null;
  }
  return (
    <PromoLoginCard
      icon="🏆"
      title="А где здесь вы?"
      text="Войдите и привяжите профиль своей беговой системы — увидите свою позицию в каждом из рейтингов, а ваша строка подсветится в таблицах."
      className="lb-promo-login"
    />
  );
}
