// Значок трека в строке «Пробежек»: залит — трек загружен и открывается разбор,
// пустой — приглашение приложить файл с часов или ссылку.

type RunTrackButtonProps = {
  hasTrack: boolean;
  onOpen: () => void;
};

export function RunTrackButton({ hasTrack, onOpen }: RunTrackButtonProps) {
  return (
    <button
      type="button"
      className={`run-track-btn ${hasTrack ? "has-track" : ""}`}
      title={hasTrack ? "Разбор пробежки по треку" : "Приложить трек с часов"}
      aria-label={hasTrack ? "Разбор пробежки по треку" : "Приложить трек с часов"}
      onClick={onOpen}
    >
      <svg
        viewBox="0 0 24 24"
        width="16"
        height="16"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Петля трассы: узнаваемый «круг с поворотом», а не общая иконка карты. */}
        <path d="M6.5 19c-2.5 0-4-1.5-4-3.5S4 12 6.5 12h9c2 0 3-1 3-2.5S17.5 7 15.5 7H8" />
        <circle cx="6.5" cy="7" r="2.2" />
        <circle cx="17.5" cy="19" r="2.2" />
      </svg>
    </button>
  );
}
