import { useMemo, useState } from "react";
import { DetailModal } from "./DetailModal";
import { StarRating } from "./StarRating";
import { PlatformBadge } from "./PlatformBadge";
import { ParticipationBadge } from "./ParticipationBadge";
import {
  deleteRunRating,
  putRunRating,
  type EligibleRun,
  type RunRating,
} from "../lib/api";
import { formatDateLong } from "../lib/format";

const OPTIONAL_CRITERIA = [
  {
    key: "score_organization",
    label: "Организация старта",
    hint: "Пунктуальность, качество разметки, брифинг перед стартом",
  },
  {
    key: "score_route",
    label: "Трасса и маршрут",
    hint: "Общее впечатление от трассы: виды, покрытие, разнообразие",
  },
  {
    key: "score_community",
    label: "Сообщество и организаторы",
    hint: "Доброжелательность и вовлечённость команды",
  },
] as const;

const COMMENT_MAX = 2000;

type RateRunModalProps = {
  run: EligibleRun;
  onClose: () => void;
  onSaved: (rating: RunRating) => void;
  onDeleted: () => void;
};

export function RateRunModal({ run, onClose, onSaved, onDeleted }: RateRunModalProps) {
  const existing = run.my_rating;
  const [overall, setOverall] = useState(existing?.score_overall ?? 0);
  const [organization, setOrganization] = useState(existing?.score_organization ?? 0);
  const [route, setRoute] = useState(existing?.score_route ?? 0);
  const [community, setCommunity] = useState(existing?.score_community ?? 0);
  const [comment, setComment] = useState(existing?.comment ?? "");
  const [isPublic, setIsPublic] = useState(existing?.is_public ?? false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Зафиксированная оценка (старту > 3 месяцев) — только просмотр.
  const readOnly = existing != null && !existing.editable;

  const optionalState: Record<string, [number, (v: number) => void]> = {
    score_organization: [organization, setOrganization],
    score_route: [route, setRoute],
    score_community: [community, setCommunity],
  };

  // Комментарий обязателен, если любой выставленный критерий ≤ 2★.
  const isLow = (score: number) => score > 0 && score <= 2;
  const commentRequired = isLow(overall) || isLow(organization) || isLow(route) || isLow(community);
  const trimmedComment = comment.trim();
  const canSave = useMemo(() => {
    if (overall < 1) return false;
    if (commentRequired && trimmedComment.length === 0) return false;
    return true;
  }, [overall, commentRequired, trimmedComment]);

  const handleSave = async () => {
    if (!canSave) {
      setError(
        overall < 1
          ? "Поставьте общую оценку старта"
          : "Для оценки 2★ и ниже добавьте комментарий",
      );
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const rating = await putRunRating(run.entry_id, {
        score_overall: overall,
        score_organization: organization || null,
        score_route: route || null,
        score_community: community || null,
        comment: trimmedComment || null,
        is_public: isPublic,
      });
      onSaved(rating);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить оценку");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await deleteRunRating(run.entry_id);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить оценку");
      setDeleting(false);
    }
  };

  const isVolunteer = run.participation_type === "volunteer";
  const details: string[] = [];
  if (isVolunteer) {
    if (run.volunteer_role) details.push(run.volunteer_role);
  } else {
    if (run.finish_time_display) details.push(run.finish_time_display);
    if (run.position != null) details.push(`${run.position} место`);
  }

  return (
    <DetailModal open title="Оценка старта" onClose={onClose}>
      <div className={`rate-run ${readOnly ? "rate-run-readonly" : ""}`}>
        <div className="rate-run-head">
          <span className="rate-run-location">{run.location_name}</span>
          <PlatformBadge code={run.platform_code} />
          <ParticipationBadge type={run.participation_type} />
          <span className="rate-run-date">{formatDateLong(run.event_date)}</span>
          {details.length > 0 && <span className="rate-run-details">{details.join(" · ")}</span>}
        </div>

        <div className="rate-run-criterion">
          <div className="rate-run-criterion-text">
            <span className="rate-run-criterion-label">
              Общая оценка старта <span className="rate-run-required">обязательно</span>
            </span>
            <span className="rate-run-criterion-hint">Итоговое впечатление от старта</span>
          </div>
          <StarRating value={overall} onChange={setOverall} disabled={readOnly} ariaLabel="Общая оценка старта" />
        </div>

        {OPTIONAL_CRITERIA.map((criterion) => {
          const [val, setVal] = optionalState[criterion.key];
          return (
            <div key={criterion.key} className="rate-run-criterion">
              <div className="rate-run-criterion-text">
                <span className="rate-run-criterion-label">{criterion.label}</span>
                <span className="rate-run-criterion-hint">{criterion.hint}</span>
              </div>
              <StarRating value={val} onChange={setVal} allowClear disabled={readOnly} ariaLabel={criterion.label} />
            </div>
          );
        })}

        {(!readOnly || comment.trim().length > 0) && (
          <label className="rate-run-comment-label">
            Комментарий{" "}
            {commentRequired && !readOnly && <span className="rate-run-required">обязательно</span>}
            <textarea
              className="rate-run-comment"
              value={comment}
              maxLength={COMMENT_MAX}
              rows={4}
              disabled={readOnly}
              placeholder={
                commentRequired
                  ? "Расскажите, что пошло не так"
                  : "Необязательно — поделитесь впечатлением"
              }
              onChange={(e) => setComment(e.target.value)}
            />
            {!readOnly && (
              <span className="rate-run-comment-count">
                {comment.length} / {COMMENT_MAX}
              </span>
            )}
          </label>
        )}

        <label className="rate-run-public">
          <input
            type="checkbox"
            checked={isPublic}
            disabled={readOnly}
            onChange={(e) => setIsPublic(e.target.checked)}
          />
          <span>
            Показывать моё имя в отзыве
            <span className="rate-run-criterion-hint">
              По умолчанию оценка анонимна — в рейтинге видно только звёзды
            </span>
          </span>
        </label>

        {readOnly && (
          <p className="rate-run-frozen">
            Оценка зафиксирована — исправить или удалить уже нельзя.
          </p>
        )}

        {error && <p className="error-text">{error}</p>}

        <div className="actions-row rate-run-actions">
          {readOnly ? (
            <button type="button" className="btn primary" onClick={onClose}>
              Закрыть
            </button>
          ) : (
            <>
              {existing && (
                <button
                  type="button"
                  className="btn secondary rate-run-delete"
                  disabled={deleting || saving}
                  onClick={() => void handleDelete()}
                >
                  {deleting ? "Удаление…" : "Удалить оценку"}
                </button>
              )}
              <button type="button" className="btn secondary" onClick={onClose}>
                Отмена
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={saving || deleting}
                onClick={() => void handleSave()}
              >
                {saving ? "Сохранение…" : existing ? "Сохранить" : "Оценить"}
              </button>
            </>
          )}
        </div>
      </div>
    </DetailModal>
  );
}
