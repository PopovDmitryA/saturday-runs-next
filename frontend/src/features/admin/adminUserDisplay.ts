import type { AdminUserAuthBrief, AdminUserListItem } from "../../lib/api";

const PROVIDER_LABELS: Record<string, string> = {
  telegram: "Telegram",
  vk: "VK",
  yandex: "Яндекс",
};

export function authProviderLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

export function userLoginLines(user: Pick<AdminUserListItem, "telegram_id" | "telegram_username" | "display_name" | "auth_logins">): AdminUserAuthBrief[] {
  if (user.auth_logins?.length) {
    return user.auth_logins;
  }
  if (user.telegram_username || user.telegram_id != null) {
    return [
      {
        provider: "telegram",
        label: user.telegram_username
          ? `@${user.telegram_username.replace(/^@/, "")}`
          : user.display_name || String(user.telegram_id),
        external_id: String(user.telegram_id ?? ""),
      },
    ];
  }
  return [];
}

export function userLoginPrimary(user: Pick<AdminUserListItem, "telegram_id" | "telegram_username" | "display_name" | "auth_logins">): string {
  const logins = userLoginLines(user);
  if (logins.length === 0) {
    return "—";
  }
  const first = logins[0];
  return `${authProviderLabel(first.provider)}: ${first.label}`;
}

export function telegramProfileUrl(user: Pick<AdminUserListItem, "telegram_username">): string | null {
  if (!user.telegram_username) {
    return null;
  }
  return `https://t.me/${user.telegram_username.replace(/^@/, "")}`;
}

export function authLoginUrl(
  login: AdminUserAuthBrief,
  user: Pick<AdminUserListItem, "telegram_username">,
): string | null {
  if (login.provider === "telegram") {
    return telegramProfileUrl(user);
  }
  if (login.provider === "vk" && login.external_id) {
    return `https://vk.com/id${login.external_id}`;
  }
  return null;
}
