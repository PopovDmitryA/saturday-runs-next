/**
 * Единый экран отказа для всех страниц кабинета: объясняет, кому раздел
 * открыт и куда идти дальше, вместо голого «403».
 */
export function OrganizerDenied({ slug, notFound }: { slug: string; notFound: boolean }) {
  if (notFound) {
    return (
      <div className="card">
        <p className="muted">Локация не найдена.</p>
        <p>
          <a href="/organizer">← Мои локации</a>
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <h2 className="section-title">Кабинет закрыт для этой локации</h2>
      <p>
        Расширенные данные локации видит её оргкоманда: те, кто хоть раз выходил здесь
        организатором, — и те, кому доступ выдали вручную.
      </p>
      <p className="muted">
        Если вы из оргкоманды, а раздел закрыт — проверьте, привязан ли профиль вашей беговой
        системы в <a href="/settings">настройках</a>: доступ считается по волонтёрствам в
        протоколах.
      </p>
      <p>
        <a className="btn secondary btn-sm" href="/organizer">
          Мои локации
        </a>{" "}
        <a className="btn btn-ghost btn-sm" href={`/locations/${slug}`}>
          Публичная страница локации
        </a>
      </p>
    </div>
  );
}
