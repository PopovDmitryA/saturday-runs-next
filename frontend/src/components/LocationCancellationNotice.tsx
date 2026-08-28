type LocationCancellationNoticeProps = {
  isPaused?: boolean;
  isCancelled?: boolean;
  reason?: string | null;
};

/**
 * Плашка «в эту субботу не бегут» на странице локации.
 *
 * Отмена — единственный статус площадки со сроком годности: она про одну
 * ближайшую субботу, и человек, который открыл страницу в пятницу, должен
 * увидеть её раньше, чем таблицы и рекорды. Плашка-чип в заголовке для этого
 * мелковата, поэтому здесь отдельный блок с причиной словами организатора
 * (её пишет s95; у 5 вёрст в реестре только сама пометка).
 *
 * У закрытой площадки не показываем: там «не действует» и так сильнее, а
 * сообщать про отменённую субботу тому, кто вообще больше не бегает, незачем.
 */
export function LocationCancellationNotice({
  isPaused,
  isCancelled,
  reason,
}: LocationCancellationNoticeProps) {
  if (!isCancelled || isPaused) {
    return null;
  }
  const text = (reason ?? "").trim();
  return (
    <div className="loc-cancel-notice" role="status">
      <span className="loc-cancel-notice-icon" aria-hidden="true">
        🚫
      </span>
      <p className="loc-cancel-notice-text">
        <strong>Ближайший старт отменён.</strong>
        {text ? ` ${text}` : ""}
      </p>
    </div>
  );
}
