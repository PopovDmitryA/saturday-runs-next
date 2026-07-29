import { useEffect, useRef, useState } from "react";
import { deleteAvatar, getCurrentUser, uploadAvatar, type User } from "../../lib/api";
import { userLabel } from "../../lib/userLabel";
import { AvatarCropModal } from "./AvatarCropModal";
import { ImageLightbox } from "../../components/ImageLightbox";

/**
 * Настройки → «Аватар»: выбор фото, кадрирование (зум/перетаскивание) в
 * AvatarCropModal, предпросмотр и удаление. Сжатие и хранение — забота
 * сервера, пользователю об этом знать не нужно.
 */
export function AvatarSection() {
  const [user, setUser] = useState<User | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((current) => {
        if (!cancelled) setUser(current);
      })
      .catch(() => {
        // страница под гейтом — сюда попадаем только в гонках при разлогине
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCropConfirm = async (blob: Blob) => {
    setBusy(true);
    setError(null);
    try {
      const cropped = new File([blob], "avatar.jpg", { type: "image/jpeg" });
      setUser(await uploadAvatar(cropped));
      setPendingFile(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить аватар");
      setPendingFile(null);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    setBusy(true);
    setError(null);
    try {
      setUser(await deleteAvatar());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить аватар");
    } finally {
      setBusy(false);
    }
  };

  const initials = user
    ? userLabel(user)
        .replace(/^@/, "")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0])
        .join("")
        .toUpperCase()
    : "";

  return (
    <section className="card">
      <h2 className="section-title">Аватар</h2>
      <div className="avatar-settings-row">
        <div className="avatar-settings-preview-wrap">
          {user?.avatar_url ? (
            // Клик открывает оригинал без пережатия (у старых аватарок —
            // то же превью, оригинала для них не существует).
            <button
              type="button"
              className="avatar-settings-preview avatar-settings-preview-button"
              onClick={() => setZoomed(true)}
              aria-label="Открыть аватар"
              title="Открыть аватар"
            >
              <img src={user.avatar_url} alt="Ваш аватар" />
            </button>
          ) : (
            <span className="avatar-settings-preview avatar-settings-placeholder" aria-hidden="true">
              {initials}
            </span>
          )}
          {busy && (
            // Загрузка идёт на сервер и обычно занимает секунду-две — без
            // этой плашки было непонятно, происходит ли что-то вообще.
            <span className="avatar-settings-busy" role="status">
              Загрузка…
            </span>
          )}
        </div>
        <div className="avatar-settings-controls">
          <p className="muted">Виден в сайдбаре кабинета и в шапке сайта.</p>
          <div className="avatar-settings-buttons">
            <button
              type="button"
              className="btn secondary"
              disabled={busy || !user}
              onClick={() => fileInputRef.current?.click()}
            >
              {busy
                ? "Загрузка…"
                : user?.avatar_url
                  ? "Заменить фото"
                  : "Загрузить фото"}
            </button>
            {user?.avatar_url && (
              <button type="button" className="btn secondary" disabled={busy} onClick={() => void handleDelete()}>
                Удалить
              </button>
            )}
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setError(null);
            setPendingFile(file);
          }
          // Позволяет выбрать тот же файл повторно.
          event.target.value = "";
        }}
      />
      {zoomed && user?.avatar_url && (
        <ImageLightbox
          src={user.avatar_full_url || user.avatar_url}
          alt="Ваш аватар"
          onClose={() => setZoomed(false)}
        />
      )}
      {pendingFile && (
        <AvatarCropModal
          file={pendingFile}
          busy={busy}
          onCancel={() => setPendingFile(null)}
          onConfirm={(blob) => void handleCropConfirm(blob)}
        />
      )}
    </section>
  );
}
