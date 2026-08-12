type EmptyActivityStateProps = {
  /** Родительный падеж: «Волонтёрств», «Пробежек» — подставляется в «… пока нет». */
  activityLabel: string;
  /** Подсказка для своего кабинета: что сделать, чтобы записи появились. */
  ownerHint: string;
  /** Подсказка в публичном профиле: там читатель — не владелец, звать его некуда. */
  publicHint: string;
  hasProfileLink: boolean;
  /** Публичный профиль чужого человека: без CTA — привязывать и обновлять нечего. */
  isPublicProfile?: boolean;
};

export function EmptyActivityState({
  activityLabel,
  ownerHint,
  publicHint,
  hasProfileLink,
  isPublicProfile = false,
}: EmptyActivityStateProps) {
  if (isPublicProfile) {
    return (
      <div className="card">
        <p>{activityLabel} пока нет.</p>
        <p className="muted">{publicHint}</p>
      </div>
    );
  }

  if (!hasProfileLink) {
    return (
      <div className="card">
        <p>{activityLabel} пока нет.</p>
        <p className="muted">Привяжите профиль системы — и записи подтянутся сюда.</p>
        <a className="btn primary" href="/dashboard#profiles">
          Привязать профиль
        </a>
      </div>
    );
  }

  return (
    <div className="card">
      <p>{activityLabel} пока нет.</p>
      <p className="muted">{ownerHint}</p>
      <a className="btn secondary" href="/dashboard#profiles">
        Обновить данные
      </a>
    </div>
  );
}
