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
    note: "Без ролей, которые можно выполнить из дома",
  },
  {
    value: "on_site_no_run",
    title: "На площадке, без совмещения с бегом",
    note: "Строгий зачёт: человек пришёл и работал вместо забега",
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
      title: "На площадке, но забег не мешает",
      note: "Роль до, после или во время дистанции",
      items: roles.filter((role) => role.on_site && role.runnable),
    },
    {
      title: "Можно выполнить не приходя",
      note: "Сюда же попадают роли, о которых мы ничего не знаем",
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
    if (draftPreset === "all") {
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
          В сообществе спорят, что считать волонтёрством: одни приходят на площадку и работают
          вместо бега, другие берут роль, которую можно сделать из дома, третьи и бегут, и
          волонтёрят. Соберите рейтинг по своим правилам.
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

        <p className="vrm-note muted">
          У parkrun дат волонтёрств нет — только сводка кредитов по ролям в профиле. С фильтром
          его волонтёрства считаются суммой кредитов выбранных ролей, поэтому день, в который
          человек взял две роли, посчитается дважды.
        </p>

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
