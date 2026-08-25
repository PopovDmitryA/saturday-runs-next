import type { User } from "./api";

/**
 * Имя пользователя для шапки.
 *
 * С 25.08.2026 display_name считается на сервере из профилей беговых систем
 * (5 вёрст / S95 / RunPark / parkrun), поэтому выбирать здесь больше нечего —
 * остались только запасные варианты на случай, если имени нет вовсе.
 */
export function userLabel(user: User): string {
  const name = user.display_name?.trim();
  if (name) {
    return name;
  }
  if (user.telegram_username) {
    return `@${user.telegram_username.replace(/^@/, "")}`;
  }
  return `Участник ${user.telegram_id ?? user.id.slice(0, 8)}`;
}
