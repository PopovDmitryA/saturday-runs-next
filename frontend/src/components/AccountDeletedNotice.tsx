import { useEffect, useState } from "react";
import { Snackbar } from "./Snackbar";

/**
 * Прощальное «Профиль удалён» на первой странице после удаления.
 *
 * Раздел «Удаление профиля» уводит на главную полной перезагрузкой (кэши и
 * память вкладки должны начаться с чистого листа), поэтому сказать «готово»
 * сам уже не успевает. Он оставляет метку в sessionStorage этой вкладки, а
 * этот компонент — смонтирован один раз на весь сайт (App.tsx) — её читает,
 * стирает и показывает сообщение. Метку не кладём в адрес: «?deleted=1»
 * попал бы в историю, закладки и пересылаемые ссылки.
 */
export const ACCOUNT_DELETED_FLAG = "sr_account_deleted";

/** Оставить метку для следующей страницы этой вкладки. */
export function markAccountDeleted(): void {
  try {
    window.sessionStorage.setItem(ACCOUNT_DELETED_FLAG, "1");
  } catch {
    // без sessionStorage просто не будет прощального сообщения
  }
}

function takeAccountDeletedFlag(): boolean {
  try {
    const value = window.sessionStorage.getItem(ACCOUNT_DELETED_FLAG);
    window.sessionStorage.removeItem(ACCOUNT_DELETED_FLAG);
    return value === "1";
  } catch {
    return false;
  }
}

export function AccountDeletedNotice() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (takeAccountDeletedFlag()) {
      setOpen(true);
    }
  }, []);

  return (
    <Snackbar open={open} title="Профиль удалён" onDismiss={() => setOpen(false)} autoHideMs={10000}>
      Спасибо, что были с нами. Результаты в протоколах остались на месте — вернуться можно в
      любой момент.
    </Snackbar>
  );
}
