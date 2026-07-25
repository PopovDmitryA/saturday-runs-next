import { PromoLoginCard } from "../../components/PromoLoginCard";
import { useOptionalUser } from "../../lib/useOptionalUser";

/**
 * Продающий баннер для анонима в разделе «Рейтинги»: раздел открыт без логина,
 * но свою строку и позицию видит только залогиненный. Залогиненному (и пока
 * сессия проверяется) не рендерится вовсе.
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
    />
  );
}
