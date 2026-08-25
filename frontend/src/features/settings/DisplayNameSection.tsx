import { useCallback, useEffect, useState } from "react";
import {
  getDisplayNameOptions,
  keepDisplayName,
  setDisplayNamePreferences,
  type DisplayNameOptions,
} from "../../lib/api";

/**
 * «Имя на сайте» — единственное место, где имя настраивается.
 *
 * Свободного ввода нет: имя берётся из профиля беговой системы, человек выбирает
 * систему-источник и вид записи. Система фиксируется при привязке профиля и сама
 * по себе не меняется — если алгоритм считает, что подошла бы другая, приходит
 * предложение, а не молчаливая подмена.
 */
// Якорь для ссылок из сайдбара и плашек: /settings#display-name.
export const DISPLAY_NAME_ANCHOR = "display-name";

export function DisplayNameSection() {
  const [options, setOptions] = useState<DisplayNameOptions | null>(null);
  const [style, setStyle] = useState<"auto" | "initial">("auto");
  // "" — «выбирать автоматически», иначе код системы.
  const [source, setSource] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await getDisplayNameOptions();
      setOptions(loaded);
      setStyle(loaded.style === "initial" ? "initial" : "auto");
      setSource(loaded.source_manual && loaded.source ? loaded.source : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить настройки имени");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Пришли по /settings#display-name: раздел рисуется только после ответа
  // сервера, поэтому к моменту разбора адреса браузером его ещё нет и штатная
  // прокрутка к якорю не срабатывает. Доезжаем сами, один раз после загрузки.
  useEffect(() => {
    if (loading || window.location.hash !== `#${DISPLAY_NAME_ANCHOR}`) {
      return;
    }
    const section = document.getElementById(DISPLAY_NAME_ANCHOR);
    section?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  }, [loading]);

  const save = async (next: { style: "auto" | "initial"; source: string }) => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await setDisplayNamePreferences({
        style: next.style,
        platform_code: next.source || null,
      });
      setSaved(true);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить имя");
    } finally {
      setSaving(false);
    }
  };

  const handleKeep = async () => {
    setSaving(true);
    setError(null);
    try {
      await keepDisplayName();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скрыть предложение");
    } finally {
      setSaving(false);
    }
  };

  // Имя, которое получится при выбранных настройках, — показываем до сохранения.
  const chosen =
    (source && options?.sources.find((item) => item.platform_code === source)) ||
    options?.sources.find((item) => item.platform_code === options.auto_source) ||
    options?.sources[0] ||
    null;
  const preview = chosen ? (style === "initial" ? chosen.name_initial : chosen.name) : options?.current;

  return (
    <section className="card display-name-card" id={DISPLAY_NAME_ANCHOR}>
      <h2 className="section-title">Имя на сайте</h2>
      {loading && <p className="muted">Загрузка…</p>}
      {error && <p className="error-text">{error}</p>}

      {!loading && options && (
        <>
          <p className="muted settings-lead">
            Имя в рейтингах, протоколах и на страницах локаций берётся из вашего профиля в беговой
            системе — так оно совпадает с тем, под каким вас записывают на стартах.
          </p>

          {options.sources.length === 0 ? (
            <p className="muted">
              Привяжите профиль 5 вёрст, S95, RunPark или parkrun — и имя подтянется оттуда.
            </p>
          ) : (
            <>
              {options.suggestion && (
                <div className="banner warning display-name-suggestion">
                  <p>
                    В системе «{options.suggestion.source_title}» вы записаны как{" "}
                    <strong>{options.suggestion.name}</strong>. Сейчас на сайте показывается{" "}
                    <strong>{options.current}</strong>. Имя менять не стали — решайте сами.
                  </p>
                  <div className="display-name-suggestion-actions">
                    <button
                      type="button"
                      className="btn secondary btn-sm"
                      disabled={saving}
                      onClick={() =>
                        void save({ style, source: options.suggestion?.platform_code ?? "" })
                      }
                    >
                      Показывать «{options.suggestion.name}»
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={saving}
                      onClick={() => void handleKeep()}
                    >
                      Оставить как есть
                    </button>
                  </div>
                </div>
              )}

              <div className="display-name-field">
                <label className="display-name-label" htmlFor="display-name-source">
                  Откуда брать имя
                </label>
                <select
                  id="display-name-source"
                  className="input"
                  value={source}
                  disabled={saving}
                  onChange={(event) => {
                    setSource(event.target.value);
                    void save({ style, source: event.target.value });
                  }}
                >
                  {/* «Автоматически» — источник пересматривается при каждой новой
                      привязке. Выбор конкретной системы такой пересмотр отключает. */}
                  <option value="">
                    Автоматически{options.auto_name ? ` — ${options.auto_name}` : ""}
                  </option>
                  {options.sources.map((item) => (
                    <option key={item.platform_code} value={item.platform_code ?? ""}>
                      {item.source_title}: {item.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="display-name-field">
                <span className="display-name-label">Как записывать</span>
                <label className="display-name-choice">
                  <input
                    type="radio"
                    name="display-name-style"
                    checked={style === "auto"}
                    disabled={saving}
                    onChange={() => {
                      setStyle("auto");
                      void save({ style: "auto", source });
                    }}
                  />
                  <span>Полное имя{chosen ? ` — ${chosen.name}` : ""}</span>
                </label>
                <label className="display-name-choice">
                  <input
                    type="radio"
                    name="display-name-style"
                    checked={style === "initial"}
                    disabled={saving}
                    onChange={() => {
                      setStyle("initial");
                      void save({ style: "initial", source });
                    }}
                  />
                  <span>Фамилия одной буквой{chosen ? ` — ${chosen.name_initial}` : ""}</span>
                </label>
              </div>

              <p className="display-name-preview">
                {/* Каждое переключение сохраняется сразу, поэтому это не
                    «будет», а текущее состояние. */}
                На сайте вы показываетесь как <strong>{preview}</strong>
                {saved && <span className="display-name-saved"> — сохранено</span>}
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
