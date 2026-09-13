/**
 * URL профиля участника на внешней беговой системе по данным привязки.
 *
 * Особый случай — RunPark: у него в external_url лежит штрихкод, а не ссылка,
 * поэтому адрес страницы кармы собирается из external_user_id. Раньше эта
 * логика дублировалась в ProfileLinkSection и AdminUsersPage.
 *
 * Ссылка собирается только по идентификатору аккаунта (GUID) — на него
 * страница кармы и открывается. Ключи «barcode:A…» и «anon:…» придуманы нами
 * самими, чтобы различать строки протокола без аккаунта: у таких людей
 * профиля на RunPark попросту нет, и ссылка вида /Account/Karmas/barcode:A…
 * вела в никуда. На проде таких привязок было 465 (Дмитрий 14.09.2026);
 * в participants.profile_url им там же проставлен NULL.
 */
const RUNPARK_ACCOUNT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function platformProfileUrl(link: {
  platform_code: string;
  external_user_id: string;
  external_url: string;
}): string | null {
  if (link.platform_code === "runpark") {
    return RUNPARK_ACCOUNT_ID.test(link.external_user_id ?? "")
      ? `https://runpark.ru/Account/Karmas/${link.external_user_id}`
      : null;
  }
  return link.external_url.startsWith("http") ? link.external_url : null;
}
