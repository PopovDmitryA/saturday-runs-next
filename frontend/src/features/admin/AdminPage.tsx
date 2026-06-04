import { AppShell } from "../../components/AppShell";
import { RequireAdmin } from "../../components/RequireAdmin";
import { AdminSubnav } from "./AdminSubnav";

function AdminHomeContent() {
  return (
    <AppShell title="Админка" activePath="/admin">
      <AdminSubnav activePath="/admin" />

      <section className="card admin-tiles">
        <h2 className="section-title">Разделы</h2>
        <div className="admin-tile-grid">
          <a href="/admin/queue" className="admin-tile">
            <span className="admin-tile-title">Очередь синхронизаций</span>
            <span className="muted admin-tile-desc">
              Celery-задачи и статусы обновления данных пользователей.
            </span>
          </a>
          <a href="/admin/users" className="admin-tile">
            <span className="admin-tile-title">Пользователи</span>
            <span className="muted admin-tile-desc">
              Поиск участников сервиса, привязки профилей и просмотр их личного кабинета.
            </span>
          </a>
          <a href="/admin/s95-participants" className="admin-tile">
            <span className="admin-tile-title">S95 — участники</span>
            <span className="muted admin-tile-desc">
              Реестр профилей S95: дата обновления, «Собирается в» и статус синхронизации.
            </span>
          </a>
          <a href="/admin/stats" className="admin-tile">
            <span className="admin-tile-title">Статистика сайта</span>
            <span className="muted admin-tile-desc">
              Пользователи, привязки, посещаемость и активность за выбранный период.
            </span>
          </a>
          <a href="/admin/parkrun" className="admin-tile">
            <span className="admin-tile-title">Parkrun — капча</span>
            <span className="muted admin-tile-desc">
              Пройти капчу в Chrome, сохранить cookies и обработать очередь профилей.
            </span>
          </a>
          <a href="/admin/abuse" className="admin-tile">
            <span className="admin-tile-title">Блокировки</span>
            <span className="muted admin-tile-desc">
              IP и Telegram-аккаунты: просмотр автоблокировок, ручной бан и разблокировка.
            </span>
          </a>
        </div>
      </section>
    </AppShell>
  );
}

export function AdminPage() {
  return <RequireAdmin>{() => <AdminHomeContent />}</RequireAdmin>;
}
