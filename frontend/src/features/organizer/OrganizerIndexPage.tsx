import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { TableWrap } from "../../components/tableUx/TableWrap";
import { PlatformBadge } from "../../components/PlatformBadge";
import { type OrganizerLocationItem, type User } from "../../lib/api";
import { PORTAL_LOGIN_HREF } from "../../lib/portalRoutes";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { loadOrganizerLocations } from "../portal/nav/OrganizerSwitcher";
import { replaceAppPath } from "../portal/nav/replaceAppPath";
import { ORGANIZER_LABEL } from "../portal/nav/siteNav";

const ACCESS_SOURCE_LABELS: Record<OrganizerLocationItem["access_source"], string> = {
  volunteering: "вы были организатором",
  manual: "доступ выдан вручную",
  both: "вы были организатором",
  admin: "доступ администратора",
};

function OrganizerIndexContent({ user }: { user: User }) {
  const [items, setItems] = useState<OrganizerLocationItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Поиск нужен админу: у него в списке весь каталог, у организатора 1–2 строки.
  const [query, setQuery] = useState("");
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Тот же общий запрос, что у переключателя в колонке и у шапки: список
    // приходит один раз на документ, а не на каждый компонент (code-7).
    loadOrganizerLocations(user.id)
      .then((list) => {
        if (cancelled) {
          return;
        }
        // Одна своя локация — выбирать не из чего, сразу открываем её кабинет
        // (решение Дмитрия 23.09.2026). Заменой записи истории, а не новым
        // переходом: иначе «назад» из кабинета возвращал бы на этот список и
        // тут же снова уводил вперёд. И без перезагрузки страницы: раньше тут
        // был location.replace — вторая полная загрузка сайта (code-8).
        // Обычно сюда уже не попадают: «Оргкабинет» при одной локации ведёт
        // прямо в неё. Админу список нужен всегда — у него весь каталог.
        if (list.length === 1 && !user.is_admin) {
          setRedirecting(true);
          replaceAppPath(`/organizer/${encodeURIComponent(list[0].slug)}`);
          return;
        }
        setItems(list);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить список локаций");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [user.id, user.is_admin]);

  if (redirecting) {
    return (
      <PortalSectionShell sidebar={{ active: "organizer" }}>
        <p className="muted">Открываем кабинет вашей локации…</p>
      </PortalSectionShell>
    );
  }

  return (
    <PortalSectionShell sidebar={{ active: "organizer" }}>
      <header className="loc-header">
        <div className="loc-header-title">
          <h1>{ORGANIZER_LABEL}: мои локации</h1>
        </div>
        <p className="muted">
          Расширенные данные по локациям для оргкоманд: свод по пробежке для отчётов и участники
          на долгой паузе.
        </p>
      </header>

      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!error && items === null && <p className="muted">Загрузка…</p>}

      {!error && items !== null && items.length === 0 && (
        <div className="card">
          <p>
            Пока здесь пусто. Доступ к кабинету появляется автоматически у тех, кто хоть раз был
            организатором на локации (по данным протоколов волонтёрств), — или выдаётся вручную.
          </p>
          <p className="muted">
            Если вы организатор, а раздел пуст — проверьте, привязан ли ваш профиль системы в{" "}
            <a href="/settings">настройках</a>, или напишите нам через страницу{" "}
            <a href="/about">«О проекте»</a>.
          </p>
          {user.is_admin && (
            <p className="muted">
              Админу кабинет любой локации доступен напрямую по адресу /organizer/&lt;slug&gt;.
            </p>
          )}
        </div>
      )}

      {!error && items !== null && items.length > 0 && (
        <div className="card">
          {items.length > 15 && (
            <p className="org-index-search">
              <input
                className="input"
                type="search"
                placeholder="Поиск по названию или городу…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </p>
          )}
          {/* Без контейнера прокрутки таблица на телефоне вылезала за карточку:
              последняя колонка с кнопкой «Открыть» уезжала за край экрана
              (Дмитрий 03.09.2026). */}
          <TableWrap>
          <table className="data-table">
            <thead>
              <tr>
                <th>Локация</th>
                <th>Системы</th>
                <th>Доступ</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items
                .filter((item) => {
                  const needle = query.trim().toLowerCase();
                  if (!needle) {
                    return true;
                  }
                  return (
                    (item.name ?? "").toLowerCase().includes(needle) ||
                    (item.city ?? "").toLowerCase().includes(needle)
                  );
                })
                .map((item) => (
                <tr key={item.location_key}>
                  <td>
                    <a href={`/organizer/${item.slug}`}>
                      <strong>{item.name}</strong>
                    </a>
                    {item.city ? <span className="muted"> — {item.city}</span> : null}
                  </td>
                  <td>
                    {item.platform_codes.map((code) => (
                      <PlatformBadge key={code} code={code} />
                    ))}
                  </td>
                  <td className="muted">{ACCESS_SOURCE_LABELS[item.access_source]}</td>
                  <td>
                    <a className="btn secondary btn-sm" href={`/organizer/${item.slug}`}>
                      Открыть
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableWrap>
        </div>
      )}
    </PortalSectionShell>
  );
}

export function OrganizerIndexPage() {
  return (
    <RequireAuth loginHref={PORTAL_LOGIN_HREF}>
      {(user) => <OrganizerIndexContent user={user} />}
    </RequireAuth>
  );
}
