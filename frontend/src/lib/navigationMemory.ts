/**
 * Память навигации: «назад» возвращает страницу такой, какой её оставили.
 *
 * Три слоя, каждый работает сам по себе:
 *  - historyEntry — ключ у записи истории, к нему привязан весь снимок;
 *  - entryMemory — сам снимок (состояние страницы) с копией в sessionStorage;
 *  - scrollMemory — позиция прокрутки и её восстановление после дорисовки.
 *
 * Ставится до монтирования React (см. main.tsx): обёртки над pushState должны
 * стоять раньше первого перехода, а обработчик popstate — раньше роутера.
 */

import { installEntryMemory } from "./entryMemory";
import { installHistoryEntries } from "./historyEntry";
import { installScrollMemory } from "./scrollMemory";

export function installNavigationMemory(): void {
  installHistoryEntries();
  installEntryMemory();
  installScrollMemory();
}
