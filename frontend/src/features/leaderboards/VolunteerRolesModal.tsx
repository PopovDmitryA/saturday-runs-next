import { useEffect, useMemo, useState } from "react";
import {
  presetRoleKeys,
  type VolunteerRoleItem,
  type VolunteerRolePreset,
} from "./leaderboardsApi";

type VolunteerRolesModalProps = {
  roles: VolunteerRoleItem[];
  preset: VolunteerRolePreset;
  selected: string[];
  onApply: (preset: VolunteerRolePreset, keys: string[]) => void;
  onClose: () => void;
};

const PRESET_LABELS: { value: VolunteerRolePreset; title: string; note: string }[] = [
  {
    value: "all",
    title: "Все роли",
    note: "Как считалось всегда: любое волонтёрство идёт в зачёт",
  },
  {
    value: "on_site",
    title: "Только на площадке",
    note: "Роли, ради которых нужно приехать на старт",
  },
  {
    value: "on_site_no_run",
    title: "На площадке, без совмещения с пробежкой",
    note: "Строгий зачёт: волонтёрство вместо забега",
  },
  {
    value: "remote",
    title: "Можно не приезжать",
    note: "Роли, которые выполняют вне площадки",
  },
  { value: "custom", title: "Свой набор", note: "Отметьте роли вручную" },
];

/** Группа ролей в списке: порядок и заголовки объясняют логику пресетов. */
function roleGroups(roles: VolunteerRoleItem[]) {
  return [
    {
      title: "На площадке, бежать нельзя",
      note: "Ядро строгого зачёта",
      items: roles.filter((role) => role.on_site && !role.runnable),
    },
    {
      title: "На площадке, но можно совмещать с пробежкой",
      note: "Роль до, после или во время дистанции",
      items: roles.filter((role) => role.on_site && role.runnable),
    },
    {
      title: "Можно выполнить не приезжая",
      note: "Присутствие на старте не требуется",
      items: roles.filter((role) => !role.on_site),
    },
  ].filter((group) => group.items.length > 0);
}

export function VolunteerRolesModal({
  roles,
  preset,
  selected,
  onApply,
  onClose,
}: VolunteerRolesModalProps) {
  const [draftPreset, setDraftPreset] = useState<VolunteerRolePreset>(preset);
  const [draftKeys, setDraftKeys] = useState<Set<string>>(() => new Set(selected));
  const groups = useMemo(() => roleGroups(roles), [roles]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const choosePreset = (value: VolunteerRolePreset) => {
    setDraftPreset(value);
    if (value === "custom") {
      return;
    }
    const keys = presetRoleKeys(value, roles);
    setDraftKeys(new Set(keys ?? roles.map((role) => role.key)));
  };

  // Ручная правка галочек — это уже «свой набор», даже если начали с пресета.
  const toggleRole = (key: string, checked: boolean) => {
    setDraftKeys((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
    setDraftPreset("custom");
  };

  const apply = () => {
    // «Все роли» — это отсутствие фильтра; выбранные вручную все роли тоже.
    if (draftPreset === "all" || draftKeys.size === roles.length) {
      onApply("all", []);
      return;
    }
    onApply(draftPreset, [...draftKeys]);
  };

  return (
    <div className="vrm-backdrop" role="presentation" onClick={onClose}>
      <div
        className="vrm-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Какие роли считать"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="vrm-head">
          <h2>Какие роли считать</h2>
          <button type="button" className="vrm-close" aria-label="Закрыть" onClick={onClose}>
            ×
          </button>
        </header>

        <p className="vrm-intro muted">
          Вы сами выбираете, из каких ролей строить рейтинг: возьмите готовый набор или
          отметьте роли вручную.
        </p>

        <div className="vrm-presets">
          {PRESET_LABELS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={`vrm-preset${draftPreset === item.value ? " vrm-preset-active" : ""}`}
              aria-pressed={draftPreset === item.value}
              onClick={() => choosePreset(item.value)}
            >
              <span className="vrm-preset-title">{item.title}</span>
              <span className="vrm-preset-note">{item.note}</span>
            </button>
          ))}
        </div>

        <div className="vrm-groups">
          {groups.map((group) => (
            <section key={group.title} className="vrm-group">
              <header className="vrm-group-head">
                <span className="vrm-group-title">{group.title}</span>
                <span className="vrm-group-note muted">{group.note}</span>
              </header>
              <ul className="vrm-role-list">
                {group.items.map((role) => (
                  <li key={role.key}>
                    <label className="vrm-role">
                      <input
                        type="checkbox"
                        checked={draftKeys.has(role.key)}
                        onChange={(event) => toggleRole(role.key, event.target.checked)}
                      />
                      <span>{role.label}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className="vrm-bulk">
          <button
            type="button"
            className="vrm-bulk-btn"
            onClick={() => {
              setDraftKeys(new Set(roles.map((role) => role.key)));
              setDraftPreset("all");
            }}
          >
            Выделить все
          </button>
          <button
            type="button"
            className="vrm-bulk-btn"
            onClick={() => {
              setDraftKeys(new Set());
              setDraftPreset("custom");
            }}
          >
            Снять все
          </button>
        </div>

        <footer className="vrm-foot">
          <span className="muted vrm-count">Выбрано ролей: {draftKeys.size} из {roles.length}</span>
          <div className="vrm-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
              Отмена
            </button>
            <button type="button" className="btn btn-sm" onClick={apply}>
              Применить
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
