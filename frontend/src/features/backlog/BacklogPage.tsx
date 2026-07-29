import { useCallback, useEffect, useState, type FormEvent } from "react";
import { PortalSectionShell } from "../portal/PortalSectionShell";
import { DetailModal } from "../../components/DetailModal";
import { ImageLightbox } from "../../components/ImageLightbox";
import { PhotoAttachments } from "../../components/PhotoAttachments";
import { DonateBlock } from "../../components/DonateBlock";
import { probeCurrentUser, type User } from "../../lib/api";
import { formatRelativeTime } from "../../lib/format";
import {
  BACKLOG_CATEGORIES,
  BACKLOG_STATUS_LABELS,
  BACKLOG_TYPE_LABELS,
  MAX_BACKLOG_PHOTOS,
  createBacklogCard,
  createBacklogComment,
  deleteBacklogPhoto,
  listBacklogCards,
  listBacklogComments,
  updateBacklogCard,
  uploadBacklogPhoto,
  voteBacklogCard,
  type BacklogCard,
  type BacklogCardStatus,
  type BacklogCardType,
  type BacklogComment,
} from "./backlogApi";
import "./backlog.css";

type Draft = {
  type: BacklogCardType;
  category: string;
  title: string;
  description: string;
  is_anonymous: boolean;
};

const EMPTY_DRAFT: Draft = {
  type: "feature",
  category: "other",
  title: "",
  description: "",
  is_anonymous: false,
};

// Порядок секций в ленте: активное сверху, реализованное в самом низу. Фильтра
// по статусу нет — разбиение на секции само разводит карточки по состояниям.
const STATUS_ORDER: BacklogCardStatus[] = ["pending", "in_progress", "rejected", "done"];

const TRANSLIT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i",
  й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t",
  у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "",
  э: "e", ю: "yu", я: "ya",
};

/**
 * Адрес открытой карточки: /backlog?card=nazvanie-karto4ki-a3a84326.
 * Читаемый слаг из названия плюс восемь символов id — по ним карточка и
 * находится, так что переименование ссылку не ломает.
 */
function cardSlug(card: BacklogCard): string {
  const words = card.title
    .toLowerCase()
    .split("")
    .map((ch) => TRANSLIT[ch] ?? ch)
    .join("")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .split("-")
    .filter(Boolean)
    .slice(0, 6)
    .join("-");
  const short = card.id.replace(/-/g, "").slice(0, 8);
  return words ? `${words}-${short}` : short;
}

/** Из слага обратно в карточку: сравниваем по хвосту-идентификатору. */
function findCardBySlug(cards: BacklogCard[], slug: string): BacklogCard | null {
  const value = slug.trim().toLowerCase();
  if (!value) {
    return null;
  }
  const tail = value.split("-").pop() ?? value;
  return (
    cards.find((card) => card.id === value) ??
    cards.find((card) => card.id.replace(/-/g, "").startsWith(tail)) ??
    null
  );
}

/** Аватар автора: картинка, если загружена, иначе инициалы. Аноним — без имени. */
function AuthorAvatar({
  name,
  avatarUrl,
  fullUrl,
}: {
  name: string | null;
  avatarUrl?: string | null;
  fullUrl?: string | null;
}) {
  const [zoomed, setZoomed] = useState(false);
  if (avatarUrl) {
    // Показываем превью, а по клику раскрываем оригинал без пережатия
    // (решение Дмитрия 28.07.2026). stopPropagation — иначе клик по аватарке
    // внутри карточки заодно открывал бы саму карточку.
    return (
      <>
        <button
          type="button"
          className="backlog-avatar backlog-avatar-button"
          onClick={(event) => {
            event.stopPropagation();
            setZoomed(true);
          }}
          aria-label={name ? `Аватар: ${name}` : "Аватар"}
        >
          <img src={avatarUrl} alt="" />
        </button>
        {zoomed && (
          <ImageLightbox src={fullUrl || avatarUrl} onClose={() => setZoomed(false)} />
        )}
      </>
    );
  }
  const initials = (name ?? "?")
    .replace(/^@/, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return (
    <span className="backlog-avatar backlog-avatar-initials" aria-hidden="true">
      {initials || "?"}
    </span>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function categoryLabel(key: string): string {
  return BACKLOG_CATEGORIES.find((item) => item.key === key)?.label ?? key;
}

function pluralComments(count: number): string {
  const abs = count % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return "комментариев";
  if (last === 1) return "комментарий";
  if (last >= 2 && last <= 4) return "комментария";
  return "комментариев";
}

function sortCards(items: BacklogCard[]): BacklogCard[] {
  return [...items].sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}

function commentAuthorLabel(comment: BacklogComment): string {
  if (comment.is_anonymous || !comment.author_display_name) {
    return comment.is_mine ? "Аноним (вы)" : "Аноним";
  }
  return comment.author_display_name;
}

/** Бейджи карточки: тип, категория, статус — каждый со своей палитрой. */
function CardBadges({ card }: { card: BacklogCard }) {
  return (
    <div className="backlog-card-head">
      <span className={`backlog-tag backlog-tag-type-${card.type}`}>{BACKLOG_TYPE_LABELS[card.type]}</span>
      <span className="backlog-tag backlog-tag-category">{categoryLabel(card.category)}</span>
      <span className={`backlog-tag backlog-tag-status-${card.status}`}>{BACKLOG_STATUS_LABELS[card.status]}</span>
    </div>
  );
}

function BacklogContent() {
  const [cards, setCards] = useState<BacklogCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Просмотр открыт всем; создавать карточки, голосовать и комментировать
  // можно только залогиненным — до ответа /auth/me считаем гостем.
  const [viewer, setViewer] = useState<User | null>(null);
  const isLoggedIn = viewer !== null;
  const isAdmin = viewer?.is_admin === true;

  const [typeFilter, setTypeFilter] = useState<BacklogCardType | "all">("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  // Фото новой карточки выбираются сразу, вместе с текстом: файлы ждут здесь
  // и уезжают на сервер сразу после создания карточки.
  const [draftPhotos, setDraftPhotos] = useState<File[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  // Из адреса приходит слаг (или старый id) — разрешаем его в id, когда лента
  // загрузилась: по слагу карточка ищется среди уже полученных.
  const [pendingCardSlug, setPendingCardSlug] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("card"),
  );
  const [openCardId, setOpenCardId] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, BacklogComment[]>>({});
  const [commentsLoading, setCommentsLoading] = useState<Record<string, boolean>>({});
  const [commentDraft, setCommentDraft] = useState("");
  const [commentAnon, setCommentAnon] = useState(false);
  const [commentSaving, setCommentSaving] = useState(false);

  // Редактирование карточки в модалке. editDraft !== null → форма вместо текста.
  // Доступно автору своей карточки (card.is_mine) и админу по любой.
  const [editDraft, setEditDraft] = useState<Draft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listBacklogCards({
        type: typeFilter === "all" ? undefined : typeFilter,
        category: categoryFilter === "all" ? undefined : categoryFilter,
      });
      setCards(sortCards(response.items));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить бэклог");
    } finally {
      setLoading(false);
    }
  }, [typeFilter, categoryFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // Только явный 401 означает гостя: иначе транзиентная ошибка отняла бы
    // у залогиненного голосование и комментарии.
    void probeCurrentUser().then((probe) => {
      if (probe.state === "authenticated") {
        setViewer(probe.user);
      } else if (probe.state === "guest") {
        setViewer(null);
      }
    });
  }, []);

  const loadComments = useCallback(async (cardId: string) => {
    setCommentsLoading((prev) => ({ ...prev, [cardId]: true }));
    try {
      const response = await listBacklogComments(cardId);
      setComments((prev) => ({ ...prev, [cardId]: response.items }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить комментарии");
    } finally {
      setCommentsLoading((prev) => ({ ...prev, [cardId]: false }));
    }
  }, []);

  useEffect(() => {
    if (openCardId && !comments[openCardId] && !commentsLoading[openCardId]) {
      void loadComments(openCardId);
    }
  }, [openCardId, comments, commentsLoading, loadComments]);

  useEffect(() => {
    if (!pendingCardSlug || cards.length === 0) {
      return;
    }
    const card = findCardBySlug(cards, pendingCardSlug);
    setPendingCardSlug(null);
    if (card) {
      setOpenCardId(card.id);
    }
  }, [pendingCardSlug, cards]);

  const openCard = (cardId: string) => {
    // Карточка получает собственный читаемый адрес: ссылку можно скопировать
    // из строки браузера и отправить коллегам, чтобы позвать голосовать.
    const card = cards.find((item) => item.id === cardId);
    const slug = card ? cardSlug(card) : cardId;
    window.history.replaceState(null, "", `${window.location.pathname}?card=${slug}`);
    setOpenCardId(cardId);
    setCommentDraft("");
    setCommentAnon(false);
    setEditDraft(null);
    setEditError(null);
  };

  const closeCard = () => {
    window.history.replaceState(null, "", window.location.pathname);
    setOpenCardId(null);
    setCommentDraft("");
    setCommentAnon(false);
    setEditDraft(null);
    setEditError(null);
  };

  const startEdit = (card: BacklogCard) => {
    setEditError(null);
    setEditDraft({
      type: card.type,
      category: card.category,
      title: card.title,
      description: card.description,
      is_anonymous: card.is_anonymous,
    });
  };

  const handleEditSave = async (cardId: string) => {
    if (!editDraft) return;
    const title = editDraft.title.trim();
    const description = editDraft.description.trim();
    if (!title || !description) {
      setEditError("Заголовок и описание не могут быть пустыми");
      return;
    }
    setEditSaving(true);
    setEditError(null);
    try {
      const updated = await updateBacklogCard(cardId, {
        type: editDraft.type,
        category: editDraft.category,
        title,
        description,
        is_anonymous: editDraft.is_anonymous,
      });
      setCards((prev) => sortCards(prev.map((item) => (item.id === cardId ? updated : item))));
      setEditDraft(null);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Не удалось сохранить изменения");
    } finally {
      setEditSaving(false);
    }
  };

  const handleVote = async (card: BacklogCard, value: 1 | -1) => {
    if (card.status === "done") {
      return;
    }
    if (!isLoggedIn) {
      window.location.href = "/login";
      return;
    }
    // Стрелка меняет голос ровно на 1 с клампом в [-1, 1]: с +1 «вниз» даёт 0,
    // ещё раз «вниз» — −1 (а не прыжок сразу на −1). На пределе — ничего.
    const nextValue = Math.max(-1, Math.min(1, card.my_vote + value)) as -1 | 0 | 1;
    if (nextValue === card.my_vote) {
      return;
    }
    try {
      const updated = await voteBacklogCard(card.id, nextValue);
      setCards((prev) => sortCards(prev.map((item) => (item.id === updated.id ? updated : item))));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проголосовать");
    }
  };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    if (!draft.title.trim() || !draft.description.trim()) {
      setFormError("Заполните название и описание");
      return;
    }
    setSaving(true);
    try {
      const card = await createBacklogCard(draft);
      // Отложенные файлы отправляем следом: до этого момента карточки не
      // существовало и привязывать фото было не к чему.
      for (const file of draftPhotos) {
        await uploadBacklogPhoto(card.id, file);
      }
      setDraft(EMPTY_DRAFT);
      setDraftPhotos([]);
      setFormOpen(false);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Не удалось сохранить карточку");
    } finally {
      setSaving(false);
    }
  };

  const handleComment = async (cardId: string) => {
    if (!commentDraft.trim()) return;
    setCommentSaving(true);
    try {
      const comment = await createBacklogComment(cardId, { body: commentDraft, is_anonymous: commentAnon });
      setComments((prev) => ({ ...prev, [cardId]: [...(prev[cardId] ?? []), comment] }));
      setCards((prev) =>
        prev.map((item) => (item.id === cardId ? { ...item, comment_count: item.comment_count + 1 } : item)),
      );
      setCommentDraft("");
      setCommentAnon(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить комментарий");
    } finally {
      setCommentSaving(false);
    }
  };

  const openedCard = cards.find((card) => card.id === openCardId) ?? null;

  const renderCard = (card: BacklogCard) => (
    <div
      className={`card backlog-card backlog-card-status-${card.status}`}
      id={`backlog-card-${card.id}`}
      key={card.id}
      role="button"
      tabIndex={0}
      onClick={() => openCard(card.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openCard(card.id);
        }
      }}
    >
      {/* Реализованное больше не приоритизируют — оставляем только итог. */}
      {card.status === "done" ? (
        <div className="backlog-card-votes backlog-card-votes-locked" title="Фича реализована — голосование закрыто">
          <span className="backlog-vote-score num">{card.score}</span>
          <span className="backlog-vote-locked-mark" aria-hidden="true">
            ✓
          </span>
        </div>
      ) : (
        <div className="backlog-card-votes" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            className={card.my_vote === 1 ? "backlog-vote-btn up active" : "backlog-vote-btn up"}
            aria-label="Голосовать за"
            onClick={() => void handleVote(card, 1)}
          >
            ▲
          </button>
          <span className="backlog-vote-score num">{card.score}</span>
          <button
            type="button"
            className={card.my_vote === -1 ? "backlog-vote-btn down active" : "backlog-vote-btn down"}
            aria-label="Голосовать против"
            onClick={() => void handleVote(card, -1)}
          >
            ▼
          </button>
        </div>
      )}
      <div className="backlog-card-body">
        <CardBadges card={card} />
        <h3 className="backlog-card-title">{card.title}</h3>
        <div className="backlog-card-meta muted">
          <AuthorAvatar
            name={card.is_anonymous ? null : card.author_display_name}
            avatarUrl={card.is_anonymous ? null : card.author_avatar_url}
            fullUrl={card.is_anonymous ? null : card.author_avatar_full_url}
          />
          {card.is_anonymous || !card.author_display_name ? "Аноним" : card.author_display_name}
          {" · "}
          {/* Возраст карточки, а не дата: в ленте важнее «давно ли». Точная
              дата — в подсказке; в самой карточке (модалке) остаётся дата. */}
          <span
            title={
              card.done_at
                ? `Заведена ${formatDate(card.created_at)}, реализована ${formatDate(card.done_at)}`
                : `Карточка заведена ${formatDate(card.created_at)}`
            }
          >
            {card.done_at
              ? `реализовано ${formatRelativeTime(card.done_at)}`
              : formatRelativeTime(card.created_at)}
          </span>
          {" · "}
          <span className="backlog-card-comments" title={`${card.comment_count} ${pluralComments(card.comment_count)}`}>
            {card.comment_count}
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M21 12a8 8 0 0 1-8 8H7l-4 3v-5.4A8 8 0 0 1 11 4h2a8 8 0 0 1 8 8Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </div>
      </div>
    </div>
  );

  // Ленту делим на секции по статусу — реализованное и отклонённое не мешаются
  // с активными идеями. Порядок сортировки внутри секции уже задан load().
  const groups = STATUS_ORDER.map((status) => ({
    status,
    items: cards.filter((card) => card.status === status),
  })).filter((group) => group.items.length > 0);

  return (
    <>
      <div className="backlog-hero">
        <section className="card backlog-hero-promo">
          <h2 className="section-title">Бэклог</h2>
          <p>
            Здесь вы напрямую влияете на то, каким станет сайт. Предлагайте новые фичи,
            сообщайте о багах, голосуйте за чужие идеи и обсуждайте их в комментариях.
          </p>
          <p>
            Чем больше голосов набирает карточка, тем выше её приоритет — самые
            востребованные идеи я беру в работу раньше остальных. Статусы показывают, что
            происходит с каждым предложением: от «на рассмотрении» до «реализовано».
          </p>
          {isLoggedIn ? (
            <button type="button" className="btn primary" onClick={() => setFormOpen((prev) => !prev)}>
              {formOpen ? "Свернуть форму" : "+ Предложить идею"}
            </button>
          ) : (
            <a className="btn primary" href="/login">
              Войдите, чтобы предложить идею
            </a>
          )}
        </section>

        <section className="card donate-block-panel backlog-hero-donate" aria-label="Поддержать проект">
          <DonateBlock variant="backlog" />
        </section>
      </div>

      {isLoggedIn && formOpen && (
        <section className="card">
          <form className="backlog-form" onSubmit={(event) => void handleCreate(event)}>
            <div className="backlog-form-row">
              <label className="field">
                <span className="field-label">Тип</span>
                <div className="backlog-segmented">
                  <button
                    type="button"
                    className={draft.type === "feature" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
                    onClick={() => setDraft({ ...draft, type: "feature" })}
                  >
                    Фича
                  </button>
                  <button
                    type="button"
                    className={draft.type === "bug" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
                    onClick={() => setDraft({ ...draft, type: "bug" })}
                  >
                    Баг
                  </button>
                </div>
              </label>
              <label className="field">
                <span className="field-label">Категория</span>
                <select
                  className="input"
                  value={draft.category}
                  onChange={(event) => setDraft({ ...draft, category: event.target.value })}
                >
                  {BACKLOG_CATEGORIES.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="field">
              <span className="field-label">Название</span>
              <input
                className="input"
                type="text"
                maxLength={200}
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                placeholder="Коротко, в одну строку"
              />
            </label>
            <label className="field">
              <span className="field-label">Описание</span>
              <textarea
                className="input"
                rows={4}
                value={draft.description}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                placeholder="Что предлагаете или что сломалось — чем подробнее, тем лучше"
              />
            </label>
            {/* Админ пишет только под своим именем — бэкенд всё равно снимет флаг. */}
            {!isAdmin && (
              <label className="login-consent-field">
                <input
                  type="checkbox"
                  checked={draft.is_anonymous}
                  onChange={(event) => setDraft({ ...draft, is_anonymous: event.target.checked })}
                />
                <span>Отправить анонимно (имя не будет показано другим пользователям)</span>
              </label>
            )}
            <PhotoAttachments
              photos={[]}
              maxPhotos={MAX_BACKLOG_PHOTOS}
              label="Фото (необязательно)"
              pending={draftPhotos}
              onPendingChange={setDraftPhotos}
              hint="Скриншот бага или пример — фото загрузятся вместе с карточкой"
            />
            {formError && (
              <div className="profile-form-error" role="alert">
                <p>{formError}</p>
              </div>
            )}
            <div className="actions-row">
              <button type="submit" className="btn primary" disabled={saving}>
                {saving
                  ? draftPhotos.length > 0
                    ? "Отправка и загрузка фото…"
                    : "Отправка…"
                  : "Отправить"}
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="card backlog-filters">
        <div className="backlog-segmented">
          <button
            type="button"
            className={typeFilter === "all" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
            onClick={() => setTypeFilter("all")}
          >
            Все типы
          </button>
          <button
            type="button"
            className={typeFilter === "feature" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
            onClick={() => setTypeFilter("feature")}
          >
            Фичи
          </button>
          <button
            type="button"
            className={typeFilter === "bug" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
            onClick={() => setTypeFilter("bug")}
          >
            Баги
          </button>
        </div>
        <select className="input" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
          <option value="all">Все категории</option>
          {BACKLOG_CATEGORIES.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
      </section>

      {loading && <p className="muted">Загрузка…</p>}
      {error && (
        <div className="card error">
          <p>{error}</p>
        </div>
      )}

      {!loading && !error && cards.length === 0 && (
        <p className="muted">Пока карточек нет — станьте первым, кто предложит идею.</p>
      )}

      {!loading &&
        !error &&
        groups.map((group) => (
          <section className="backlog-group" key={group.status}>
            <div className="backlog-group-head">
              <span className={`backlog-tag backlog-tag-status-${group.status}`}>
                {BACKLOG_STATUS_LABELS[group.status]}
              </span>
              <span className="backlog-group-count muted">{group.items.length}</span>
            </div>
            <div className="backlog-grid">{group.items.map(renderCard)}</div>
          </section>
        ))}

      <DetailModal
        open={openedCard !== null}
        title={openedCard?.title ?? ""}
        onClose={closeCard}
      >
        {openedCard && (
          <div className="backlog-modal">
            <CardBadges card={openedCard} />
            <div className="backlog-modal-meta muted">
              <AuthorAvatar
                name={openedCard.is_anonymous ? null : openedCard.author_display_name}
                avatarUrl={openedCard.is_anonymous ? null : openedCard.author_avatar_url}
                fullUrl={openedCard.is_anonymous ? null : openedCard.author_avatar_full_url}
              />
              {openedCard.is_anonymous || !openedCard.author_display_name ? (
                "Аноним"
              ) : (
                <a href={`/users/${openedCard.author_handle}`} target="_blank" rel="noreferrer">
                  {openedCard.author_display_name}
                </a>
              )}
              {" · "}
              {formatDate(openedCard.created_at)}
              {" · "}
              <b>{openedCard.score}</b> голосов
              {openedCard.status === "done" && " · голосование закрыто"}
            </div>

            {editDraft ? (
              <div className="backlog-form backlog-edit-form">
                <div className="backlog-form-row">
                  <label className="field">
                    <span className="field-label">Тип</span>
                    <div className="backlog-segmented">
                      <button
                        type="button"
                        className={editDraft.type === "feature" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
                        onClick={() => setEditDraft({ ...editDraft, type: "feature" })}
                      >
                        Фича
                      </button>
                      <button
                        type="button"
                        className={editDraft.type === "bug" ? "backlog-segmented-btn active" : "backlog-segmented-btn"}
                        onClick={() => setEditDraft({ ...editDraft, type: "bug" })}
                      >
                        Баг
                      </button>
                    </div>
                  </label>
                  <label className="field">
                    <span className="field-label">Категория</span>
                    <select
                      className="input"
                      value={editDraft.category}
                      onChange={(event) => setEditDraft({ ...editDraft, category: event.target.value })}
                    >
                      {BACKLOG_CATEGORIES.map((item) => (
                        <option key={item.key} value={item.key}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="field">
                  <span className="field-label">Заголовок</span>
                  <input
                    className="input"
                    value={editDraft.title}
                    maxLength={200}
                    onChange={(event) => setEditDraft({ ...editDraft, title: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Описание</span>
                  <textarea
                    className="input"
                    rows={4}
                    value={editDraft.description}
                    onChange={(event) => setEditDraft({ ...editDraft, description: event.target.value })}
                  />
                </label>
                {!isAdmin && (
                  <label className="login-consent-field">
                    <input
                      type="checkbox"
                      checked={editDraft.is_anonymous}
                      onChange={(event) => setEditDraft({ ...editDraft, is_anonymous: event.target.checked })}
                    />
                    <span>Отправить анонимно (имя не будет показано другим пользователям)</span>
                  </label>
                )}
                {editError && (
                  <div className="profile-form-error" role="alert">
                    <p>{editError}</p>
                  </div>
                )}
                <div className="actions-row">
                  <button
                    type="button"
                    className="btn primary btn-sm"
                    disabled={editSaving}
                    onClick={() => void handleEditSave(openedCard.id)}
                  >
                    {editSaving ? "Сохранение…" : "Сохранить"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={editSaving}
                    onClick={() => {
                      setEditDraft(null);
                      setEditError(null);
                    }}
                  >
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className="backlog-card-description">{openedCard.description}</p>

            <PhotoAttachments
              photos={openedCard.photos ?? []}
              maxPhotos={MAX_BACKLOG_PHOTOS}
              readOnly={!isLoggedIn || (!openedCard.is_mine && !isAdmin)}
              label="Фото к карточке"
              onUpload={(file) => uploadBacklogPhoto(openedCard.id, file)}
              onDelete={deleteBacklogPhoto}
              onChange={(photos) =>
                setCards((prev) =>
                  prev.map((item) => (item.id === openedCard.id ? { ...item, photos } : item)),
                )
              }
            />
                {(openedCard.is_mine || isAdmin) && (
                  <div className="actions-row backlog-edit-actions">
                    <button type="button" className="btn btn-sm" onClick={() => startEdit(openedCard)}>
                      Редактировать
                    </button>
                  </div>
                )}
              </>
            )}

            <div className="backlog-comments">
              <h4>Обсуждение</h4>
              {commentsLoading[openedCard.id] && <p className="muted">Загрузка…</p>}

              <div className="backlog-chat">
                {(comments[openedCard.id] ?? []).map((comment) => (
                  <div
                    className={`backlog-chat-row ${comment.is_mine ? "mine" : "theirs"}`}
                    key={comment.id}
                  >
                    <div
                      className={`backlog-bubble ${comment.is_mine ? "mine" : "theirs"} ${
                        comment.is_admin_author ? "admin" : ""
                      }`}
                    >
                      <div className="backlog-bubble-head">
                        <AuthorAvatar
                          name={comment.is_anonymous ? null : comment.author_display_name}
                          avatarUrl={comment.is_anonymous ? null : comment.author_avatar_url}
                          fullUrl={comment.is_anonymous ? null : comment.author_avatar_full_url}
                        />
                        <span className="backlog-bubble-author">{commentAuthorLabel(comment)}</span>
                        {comment.is_admin_author && <span className="backlog-tag backlog-tag-admin">Админ</span>}
                      </div>
                      <p className="backlog-bubble-body">{comment.body}</p>
                      <span className="backlog-bubble-time">{formatTime(comment.created_at)}</span>
                    </div>
                  </div>
                ))}
                {comments[openedCard.id]?.length === 0 && !commentsLoading[openedCard.id] && (
                  <p className="muted">Комментариев пока нет — начните обсуждение.</p>
                )}
              </div>

              {isLoggedIn ? (
                <div className="backlog-comment-form">
                  <textarea
                    className="input"
                    rows={2}
                    value={commentDraft}
                    onChange={(event) => setCommentDraft(event.target.value)}
                    placeholder="Ваш комментарий…"
                  />
                  <div className="backlog-comment-form-actions">
                    {!isAdmin ? (
                      <label className="login-consent-field">
                        <input
                          type="checkbox"
                          checked={commentAnon}
                          onChange={(event) => setCommentAnon(event.target.checked)}
                        />
                        <span>Анонимно</span>
                      </label>
                    ) : (
                      <span className="muted">Ответ от имени администратора сайта</span>
                    )}
                    <button
                      type="button"
                      className="btn primary btn-sm"
                      disabled={commentSaving || !commentDraft.trim()}
                      onClick={() => void handleComment(openedCard.id)}
                    >
                      {commentSaving ? "Отправка…" : "Отправить"}
                    </button>
                  </div>
                </div>
              ) : (
                <p className="muted backlog-comment-login-hint">
                  <a href="/login">Войдите</a>, чтобы прокомментировать.
                </p>
              )}
            </div>
          </div>
        )}
      </DetailModal>
    </>
  );
}

export function BacklogPage() {
  return (
    <PortalSectionShell sidebar={{ active: "backlog" }}>
      <BacklogContent />
    </PortalSectionShell>
  );
}
