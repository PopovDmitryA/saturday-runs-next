import { Fragment, useCallback, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { StatHintTooltip } from "../../components/StatHintTooltip";
import { COUNT_FORMS, formatInt, pluralFormRu, pluralizeRu } from "../../lib/format";
import type { PlanAudience, PlanMark, PlanMarkKind, PlanPerson } from "./plannerApi";
import { SERIES_ICON } from "./seriesText";
import "./planner.css";

/**
 * Кто планирует пробежать в дату: имена через запятую, под ними одной
 * строкой дети и приватные числом. Два вида:
 *
 * - страница локации (PlanPeopleCapped) — только «Анна Смирнова (132)», без
 *   меток, и не больше трёх строк имён: что не влезло, свёрнуто в «и ещё X»
 *   (решение Дмитрия 25.10.2026: блок для участников компактный, знаковые
 *   старты — дело оргкабинета). «и ещё X» раскрывает список тут же, рядом —
 *   «свернуть»; окна со всем списком нет (решение 27.10.2026: модалка на
 *   «ещё» лишняя). Одного человека в «и ещё 1» не сворачиваем — он встаёт
 *   четвёртой строкой. У постоянных («каждую субботу») — значок ↻ у имени:
 *   отдельной строки «↻ Каждую субботу: N» больше нет (решение 27.10.2026);
 * - оргкалендарь (PlanPeople) — все имена, с числом пробежек и метками
 *   «впервые у нас» и юбилеями.
 *
 * Без рамок и заголовков колонок: план один — «планирую пробежать», делить
 * людей не на что. Порядок — как отдал сервер: у кого больше пробежек, тот
 * выше. Детей и приватных сервер отдаёт только числом; у каждой из этих
 * частей своя «i» со своим объяснением: на старте бывают только дети или
 * только приватные (замечание 25.10.2026).
 */

/**
 * Глагол-подпись перед числом: «Планирует 1», но «Планируют 21».
 * pluralFormRu согласует по последней цифре и давал «Планирует 21»: у подписи
 * без существительного единственное число — только при ровно одном.
 */
export function verbForCount(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

const CHILD_FORMS = ["ребёнок", "ребёнка", "детей"] as const;
const PERSON_FORMS = ["человек", "человека", "человек"] as const;

// Потолок блока на странице локации: имён не больше трёх строк на любой ширине.
const MAX_NAME_LINES = 3;

export const PRIVATE_PLANS_HINT =
  "Имя видно, только если человек отметил план публичным. У этих людей план приватный или профиль закрыт — " +
  "показываем их только числом.";

export const CHILDREN_PLANS_HINT = "Детей до 15 лет по имени не называем — показываем только числом.";

export const FIRST_HERE_LABEL = "впервые у нас";

const MARK_HINTS: Record<PlanMarkKind, string> = {
  first_here: "Ни пробежки, ни волонтёрства на вашей локации — приедет впервые.",
  first_ever: "В протоколах ни одной пробежки — эта будет первой.",
  jubilee: "Номер пробежки по всем системам.",
  jubilee_here: "Номер пробежки на вашей локации.",
  jubilee_platform: "Номер пробежки в этой системе.",
};

/**
 * Короткие слова — неразрывным пробелом к следующему: в узком столбце метка
 * переносится «впервые / у нас» и «150-я / в 5 вёрст», а не оставляет «у» и
 * «в» висеть в конце строки.
 */
function glueShortWords(text: string): string {
  const words = text.split(" ");
  return words
    .map((word, index) => (index === words.length - 1 ? word : `${word}${word.length <= 2 ? "\u00a0" : " "}`))
    .join("");
}

/** Метка знакового старта: «впервые у нас» — зелёная, номерные — янтарные. */
export function PlanMarkTag({ mark }: { mark: PlanMark }) {
  const fresh = mark.kind === "first_here";
  return (
    <span className={fresh ? "plan-tag plan-tag-new" : "plan-tag plan-tag-jubilee"} title={MARK_HINTS[mark.kind]}>
      {glueShortWords(mark.label)}
    </span>
  );
}

export function PlanFirstHereTag() {
  return <PlanMarkTag mark={{ kind: "first_here", label: FIRST_HERE_LABEL }} />;
}

/**
 * Метки человека в оргкалендаре. Сервер отдаёт там поля first_here и jubilee;
 * пришёл готовый список marks — берём его.
 */
function personMarks(person: PlanPerson): PlanMark[] {
  if (person.marks) {
    return person.marks;
  }
  const marks: PlanMark[] = [];
  // != null, а не !== null: пропущенное в ответе поле — тоже «нечего сказать».
  if (person.jubilee != null) {
    marks.push({ kind: "jubilee", label: `${formatInt(person.jubilee)}-я пробежка` });
  }
  if (person.first_here) {
    marks.push({ kind: "first_here", label: FIRST_HERE_LABEL });
  }
  return marks;
}

/** Имя со ссылкой: по умолчанию — профиль на сайте, в оргкабинете — страница участника. */
function PersonName({ person, href }: { person: PlanPerson; href: string | null }) {
  const label = person.name?.trim() || "—";
  if (!href || label === "—") {
    return <>{label}</>;
  }
  return (
    <a className="loc-runner-link" href={href}>
      {label}
    </a>
  );
}

export const REGULAR_PERSON_HINT = "Бегает здесь каждую субботу";

/** «↻» у имени постоянного; подсказка — наведением и тапом (атрибут title). */
function RegularMark() {
  return (
    <span className="plan-people-regular" title={REGULAR_PERSON_HINT}>
      <span aria-hidden="true">{SERIES_ICON}</span>
      <span className="visually-hidden">, {REGULAR_PERSON_HINT.toLowerCase()}</span>
    </span>
  );
}

/** «(132)» — всего пробежек, тише имени; читалке — словами. */
function RunsCount({ runs }: { runs: number }) {
  return (
    <span className="plan-people-runs" title={`Всего ${pluralizeRu(runs, COUNT_FORMS.runs)}`}>
      <span aria-hidden="true">({formatInt(runs)})</span>
      <span className="visually-hidden">, всего {pluralizeRu(runs, COUNT_FORMS.runs)}</span>
    </span>
  );
}

/**
 * Имя с числом пробежек и метками. Имя с числом — одним куском, метки —
 * каждая своим: на узком блоке строка переносится между ними, и человек с
 * метками не уходит целиком на новую строку, оставляя дыру после соседа.
 * Запятая — внутри последнего куска: на перенос она не отрывается от него.
 */
function PersonEntry({
  person,
  href,
  marks,
  comma,
}: {
  person: PlanPerson;
  href: string | null;
  marks: PlanMark[];
  comma: boolean;
}) {
  const tail = comma ? "," : "";
  return (
    <span className="plan-people-person">
      <span className="plan-people-who">
        <PersonName person={person} href={href} />
        {person.regular && (
          <>
            {"\u00a0"}
            <RegularMark />
          </>
        )}
        {person.runs != null && (
          <>
            {" "}
            <RunsCount runs={person.runs} />
          </>
        )}
        {marks.length === 0 && tail}
      </span>
      {marks.map((mark, index) => (
        <Fragment key={`${mark.kind}-${index}`}>
          {" "}
          <span className="plan-people-tag-slot">
            <PlanMarkTag mark={mark} />
            {index === marks.length - 1 && tail}
          </span>
        </Fragment>
      ))}
    </span>
  );
}

function profileHref(person: PlanPerson): string | null {
  return person.handle ? `/users/${encodeURIComponent(person.handle)}` : null;
}

function personKey(person: PlanPerson, index: number): string {
  return `${person.handle ?? person.name}-${index}`;
}

/** «i» с подсказкой — та же, что у детей и приватных; оргкабинет ставит её у заголовка столбца. */
export function PlansHint({ text, label }: { text: string; label: string }) {
  return (
    <StatHintTooltip text={text} label={label} className="plan-people-hint">
      <span className="loc-stat-info" aria-hidden="true">
        i
      </span>
    </StatHintTooltip>
  );
}

/**
 * Дети и приватные — одной строкой «1 ребёнок ⓘ · и ещё 3 — приватно ⓘ».
 * named — сколько людей названо выше: при них приватные — «и ещё N».
 */
export function PlanAudienceNote({ named, children, private: privateCount }: {
  named: number;
  children: number;
  private: number;
}) {
  const childrenText = children > 0 ? `${formatInt(children)} ${pluralFormRu(children, CHILD_FORMS)}` : null;
  // Перед «приватно» есть имена или дети — «и ещё 2»; иначе приватны все и «и ещё» не к чему.
  const privateText =
    privateCount <= 0
      ? null
      : named > 0 || children > 0
        ? `и ещё ${formatInt(privateCount)} — приватно`
        : `${formatInt(privateCount)} ${pluralFormRu(privateCount, PERSON_FORMS)} — приватно`;
  if (!childrenText && !privateText) {
    return null;
  }
  // Переносится строка только между частями, своя «i» не отрывается от своей части.
  return (
    <p className="plan-people-note">
      {childrenText && (
        <span className="plan-people-note-part">
          {childrenText}
          <PlansHint text={CHILDREN_PLANS_HINT} label="Почему дети без имён" />
        </span>
      )}
      {childrenText && privateText && " · "}
      {privateText && (
        <span className="plan-people-note-part">
          {privateText}
          <PlansHint text={PRIVATE_PLANS_HINT} label="Почему часть планов приватная" />
        </span>
      )}
    </p>
  );
}

function hasAnyone(audience: PlanAudience): boolean {
  return audience.people.length > 0 || audience.children > 0 || audience.private > 0;
}

/**
 * Оргкалендарь: все имена с числом пробежек и метками, без потолка — день
 * организатор открывает, чтобы увидеть всех.
 */
export function PlanPeople({
  audience,
  personHref = profileHref,
}: {
  audience: PlanAudience;
  /** Куда ведёт имя: по умолчанию — профиль на сайте; null — без ссылки. */
  personHref?: (person: PlanPerson) => string | null;
}) {
  if (!hasAnyone(audience)) {
    return null;
  }
  const { people } = audience;
  return (
    <div className="plan-people">
      {people.length > 0 && (
        <p className="plan-people-names">
          {people.map((person, index) => (
            <Fragment key={personKey(person, index)}>
              <PersonEntry
                person={person}
                href={personHref(person)}
                marks={personMarks(person)}
                comma={index < people.length - 1}
              />{" "}
            </Fragment>
          ))}
        </p>
      )}
      <PlanAudienceNote named={people.length} children={audience.children} private={audience.private} />
    </div>
  );
}

/**
 * Нижний край третьей строки имён. Строка — line-height абзаца: элемент,
 * который начался на четвёртой, кончается ниже на целую строку, поэтому запас
 * в полстроки ловит и округления, и «и ещё X» мельче имён.
 */
function linesBottom(node: HTMLElement, lines: number): number {
  const top = node.getBoundingClientRect().top;
  let lineHeight = parseFloat(getComputedStyle(node).lineHeight);
  if (!Number.isFinite(lineHeight)) {
    lineHeight = node.querySelector(".plan-people-who")?.getBoundingClientRect().height ?? 20;
  }
  return top + lineHeight * (lines + 0.5);
}

/**
 * Сколько имён влезает в три строки вместе с «и ещё X» в конце третьей.
 * Сначала рисуем всех и находим первое имя на четвёртой строке; потом
 * убираем по одному, пока «и ещё X» не встанет в третью. Всё — в
 * useLayoutEffect, до отрисовки: глазу видно только итог, без мигания. Новая
 * ширина (поворот телефона, свернули колонку) — заново со всех, синхронно
 * (flushSync в ResizeObserver), иначе мелькнул бы полный список.
 *
 * expanded — список раскрыт («и ещё X» нажали): видны все, обрезку не
 * трогаем. Ширина сменилась, пока он раскрыт (повернули телефон), — меряем
 * после «свернуть»; та же — прежняя обрезка верна.
 */
function useNamesCap(people: PlanPerson[], expanded: boolean) {
  const nodeRef = useRef<HTMLParagraphElement | null>(null);
  const widthRef = useRef(0);
  const expandedRef = useRef(expanded);
  // null — рисуем всех и меряем; число — сколько имён показано перед «и ещё X».
  const [shown, setShown] = useState<number | null>(null);
  const [round, setRound] = useState(0);
  // Пришли другие люди (ответ обновился) — мерить заново.
  const [source, setSource] = useState(people);
  if (source !== people) {
    setSource(people);
    setShown(null);
  }

  useLayoutEffect(() => {
    const node = nodeRef.current;
    // Скрытая панель на телефоне (display: none) не меряется: её дата,
    // став выбранной, монтируется заново и меряется тогда. Раскрытый список
    // не меряем вовсе.
    if (!node || expanded || node.getClientRects().length === 0) {
      return;
    }
    widthRef.current = node.getBoundingClientRect().width;
    const bottom = linesBottom(node, MAX_NAME_LINES);
    const fits = (element: Element | null) => !element || element.getBoundingClientRect().bottom <= bottom;
    if (shown === null) {
      const entries = Array.from(node.querySelectorAll(".plan-people-person"));
      const firstOut = entries.findIndex((entry) => !fits(entry));
      if (firstOut >= 0) {
        setShown(Math.max(1, firstOut));
      }
      return;
    }
    if (shown > 1 && !fits(node.querySelector(".plan-people-more"))) {
      setShown(shown - 1);
    }
  }, [shown, people, round, expanded]);

  const attach = useCallback((node: HTMLParagraphElement | null) => {
    nodeRef.current = node;
    if (!node) {
      return;
    }
    // Только ширина: высота меняется от нашей же обрезки, и реакция на неё
    // ходила бы по кругу «все → обрезали → все».
    const observer = new ResizeObserver(() => {
      const width = node.getBoundingClientRect().width;
      if (expandedRef.current || width === 0 || Math.abs(width - widthRef.current) < 0.5) {
        return;
      }
      widthRef.current = width;
      flushSync(() => {
        setShown(null);
        setRound((value) => value + 1);
      });
    });
    observer.observe(node);
    // React 19 зовёт возвращённую функцию при отвязке узла.
    return () => {
      observer.disconnect();
      nodeRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    expandedRef.current = expanded;
    // Свернули — ширина могла смениться, пока список был раскрыт.
    const node = nodeRef.current;
    if (expanded || !node) {
      return;
    }
    const width = node.getBoundingClientRect().width;
    if (width !== 0 && Math.abs(width - widthRef.current) >= 0.5) {
      widthRef.current = width;
      setShown(null);
      setRound((value) => value + 1);
    }
  }, [expanded]);

  // За «и ещё X» не прячем ровно одного: «и ещё 1» короче имени лишь на
  // пару слов, а человека за ним не видно (замечание Дмитрия 26.10.2026).
  // Показываем его, разрешив блоку лишнюю строку. Замер этого не замечает:
  // кнопки «и ещё» в разметке нет, и ужимать дальше нечего.
  const capped = shown === null || people.length - shown <= 1 ? people.length : shown;
  return { attach, shown: capped };
}

/**
 * Страница локации: «Анна Смирнова (132), Пётр Иванов ↻ (87) и ещё 14» — не
 * больше трёх строк имён. «и ещё X» раскрывает список на месте, в конце —
 * «свернуть». Раскрыт ли он, решает страница (expanded): при смене вкладки
 * даты список сворачивается.
 *
 * Кнопка одна и та же — «и ещё X» становится «свернуть» на новом месте:
 * React переносит тот же узел, и фокус с клавиатуры остаётся на ней, а не
 * падает на <body>.
 */
export function PlanPeopleCapped({
  audience,
  expanded,
  onExpandedChange,
  personHref = profileHref,
}: {
  audience: PlanAudience;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  personHref?: (person: PlanPerson) => string | null;
}) {
  const { people } = audience;
  const { attach, shown } = useNamesCap(people, expanded);

  if (!hasAnyone(audience)) {
    return null;
  }

  const visible = expanded ? people : people.slice(0, shown);
  const rest = people.length - visible.length;
  const toggle = expanded || rest > 0;

  return (
    <div className="plan-people">
      {people.length > 0 && (
        <p className="plan-people-names" ref={attach}>
          {visible.map((person, index) => (
            <Fragment key={personKey(person, index)}>
              <PersonEntry
                person={person}
                href={personHref(person)}
                marks={[]}
                comma={index < visible.length - 1}
              />{" "}
            </Fragment>
          ))}
          {toggle && (
            <button
              key="toggle"
              type="button"
              className="link-button plan-people-more"
              aria-expanded={expanded}
              onClick={() => onExpandedChange(!expanded)}
            >
              {expanded ? "свернуть" : `и ещё ${formatInt(rest)}`}
            </button>
          )}
        </p>
      )}
      <PlanAudienceNote named={people.length} children={audience.children} private={audience.private} />
    </div>
  );
}
