import { useState, type ReactNode } from "react";

/**
 * Вся остальная статистика — под катом. На дашборде остаётся то, что
 * меняется каждую субботу; справочные цифры за всю историю нужны реже,
 * и держать их развёрнутыми — значит топить свежее в вечном.
 */
export function CabinetAllStats({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <section className="cab-allstats">
      <button
        type="button"
        className="cab-allstats-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span>Вся статистика</span>
        <span className="cab-allstats-chevron" aria-hidden="true">
          {open ? "▲" : "▼"}
        </span>
      </button>
      {open && <div className="cab-allstats-body">{children}</div>}
    </section>
  );
}
