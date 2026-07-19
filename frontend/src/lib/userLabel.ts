import type { User } from "./api";

/** Имя пользователя для шапки: кастомное → @telegram-логин → имя из привязки → «Участник N». */
export function userLabel(user: User): string {
  const customName = user.display_name?.trim();
  if (user.display_name_customized === true && customName) {
    return customName;
  }
  if (user.telegram_username) {
    const login = user.telegram_username.replace(/^@/, "");
    return `@${login}`;
  }
  if (customName) {
    return customName;
  }
  return `Участник ${user.telegram_id ?? user.id.slice(0, 8)}`;
}
