/**
 * URL профиля участника на внешней беговой системе по данным привязки.
 *
 * Особый случай — RunPark: у него в external_url лежит штрихкод, а не ссылка,
 * поэтому адрес страницы кармы собирается из external_user_id. Раньше эта
 * логика дублировалась в ProfileLinkSection и AdminUsersPage.
 */
export function platformProfileUrl(link: {
  platform_code: string;
  external_user_id: string;
  external_url: string;
}): string | null {
  if (link.platform_code === "runpark") {
    return link.external_user_id
      ? `https://runpark.ru/Account/Karmas/${link.external_user_id}`
      : null;
  }
  return link.external_url.startsWith("http") ? link.external_url : null;
}
