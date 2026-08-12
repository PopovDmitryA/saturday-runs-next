type EmptyActivityStateProps = {
  activityLabel: string;
  hasProfileLink: boolean;
};

export function EmptyActivityState({ activityLabel, hasProfileLink }: EmptyActivityStateProps) {
  if (!hasProfileLink) {
    return (
      <div className="card">
        <p>{activityLabel} пока нет.</p>
        <a className="btn primary" href="/dashboard#profiles">
          Привязать профиль
        </a>
      </div>
    );
  }

  return (
    <div className="card">
      <p>{activityLabel} не найдены в базе.</p>
      <p className="muted">Профиль привязан — попробуйте обновить данные на вкладке «Обзор».</p>
      <a className="btn primary" href="/dashboard#profiles">
        Обновить данные
      </a>
    </div>
  );
}
