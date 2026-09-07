// Постер из поста организатора.
//
// Источник данных — не API, а ТЕКСТ поста после правки организатором: он
// убирает из списка лишних (ошибочный новичок, гость, которого не надо
// называть), и постер обязан отразить именно исправленный список. Поэтому
// здесь парсер: строки поста → люди и цифры → ShareCardData.
//
// Шаблоны текста — наши же (organizer_post_service.py), парсер знает их
// структуру, но терпим к правкам: пропавший блок просто не попадает на постер,
// а число в скобках «(7)» не читается вовсе — люди считаются по именам.

import type { OrganizerEventDateItem, OrganizerPostTemplate } from "../../lib/api";
import { COUNT_FORMS, formatDate, formatInt, platformCodeLabel, pluralFormRu } from "../../lib/format";
import { humanizeName } from "../sharing/subjects";
import type { ShareCardData, ShareMetric, ShareNameList, ShareSubject } from "../sharing/types";

export type OrganizerPosterContext = {
  slug: string;
  template: OrganizerPostTemplate;
  locationName: string | null;
  /** Событие поста; у «Юбилеев завтра» и «Нужны волонтёры» его нет. */
  event: OrganizerEventDateItem | null;
};

// ── Разбор текста ────────────────────────────────────────────────────────────

/** Ведущие эмодзи строки (с селекторами вида и пробелами между ними). */
const LEADING_EMOJI = /^(?:[\p{Extended_Pictographic}\u{FE0F}\u{200D}\u{20E3}]|\s)+/u;
/** Маркеры пунктов: «• », «- », «1. », «+7 …». */
const ITEM_MARKER = /^(?:[•\-–+]|\d+\.)\s*/;
/** Эмодзи-маркеры пунктов наших шаблонов. */
const ITEM_EMOJI = ["🔹", "🔸", "❗", "✅"];
/** «Заголовок:», «Заголовок (7): имена», «Финишёров: 120». Двоеточие должно
 * стоять перед пробелом или концом строки — иначе ловится время «20:11». */
const HEADER_RE = /^(.+?)(?:\s*\((\d+)\))?:(?:\s+(.*))?$/;

type Section = {
  label: string;
  inline: string;
  /** Пункты без маркеров и эмодзи. */
  items: string[];
  /** Пункты как есть — когда важен сам маркер («❗️» / «✅», «+7»). */
  rawItems: string[];
};

type ParsedPost = {
  lines: string[];
  title: string;
  location: string | null;
  sections: Section[];
};

function cleanLine(line: string): string {
  return line.replace(/\*\*/g, "").trim();
}

function stripEmoji(text: string): string {
  return text.replace(LEADING_EMOJI, "").trim();
}

function isItemLine(line: string): boolean {
  return ITEM_MARKER.test(line) || ITEM_EMOJI.some((mark) => line.startsWith(mark));
}

function itemText(line: string): string {
  return stripEmoji(line.replace(ITEM_MARKER, ""));
}

export function parsePost(text: string): ParsedPost {
  const lines = text.split("\n").map(cleanLine);
  const sections: Section[] = [];
  let current: Section | null = null;
  for (const line of lines) {
    if (!line) {
      current = null;
      continue;
    }
    if (isItemLine(line)) {
      if (!current) {
        // Пункты без заголовка («1. Организатор — …» у волонтёров).
        current = { label: "", inline: "", items: [], rawItems: [] };
        sections.push(current);
      }
      current.items.push(itemText(line));
      current.rawItems.push(line);
      continue;
    }
    const header = HEADER_RE.exec(stripEmoji(line));
    if (header) {
      current = { label: header[1].trim(), inline: (header[3] ?? "").trim(), items: [], rawItems: [] };
      sections.push(current);
    } else if (current && !current.inline && current.items.length === 0) {
      // Список одной строкой под заголовком («Юбилейные пробежки:» ↵ «А, Б»).
      current.inline = line;
    } else {
      current = null;
    }
  }
  const firstLine = lines.find(Boolean) ?? "";
  const locationLine = lines.find((line) => line.startsWith("📍"));
  const location = locationLine
    ? stripEmoji(locationLine)
        .replace(/^Локация:\s*/i, "")
        .split(/\s+[—·]\s+/)[0]
        .trim() || null
    : null;
  return { lines, title: stripEmoji(firstLine), location, sections };
}

function dedupe(items: string[]): string[] {
  return [...new Set(items.map((item) => item.trim()).filter(Boolean))];
}

/** Фамилии в сводном посте капсом («Виктор СЕРОВ») — на постере это крик. */
function humanize(items: string[]): string[] {
  return items.map((item) => humanizeName(item) ?? item);
}

/** «А, Б, В. Гордимся каждым!» → [А, Б, В]: имена до первой точки-с-пробелом. */
function splitNames(text: string | undefined): string[] {
  const head = (text ?? "").split(/\.\s+|\.$/)[0] ?? "";
  return dedupe(head.split(/\s*[,;]\s*/));
}

function partBefore(text: string, sep = " — "): string {
  const index = text.indexOf(sep);
  return (index >= 0 ? text.slice(0, index) : text).trim();
}

function partAfter(text: string, sep = " — "): string {
  const index = text.indexOf(sep);
  return index >= 0 ? text.slice(index + sep.length).trim() : "";
}

/** «Имя (5 пробежек у нас)» → «Имя». */
function stripTrailingParen(text: string): string {
  return text.replace(/\s*\([^()]*\)\s*$/, "").trim();
}

function parseInt10(text: string | undefined): number | null {
  const match = /(\d+)/.exec(text ?? "");
  return match ? Number(match[1]) : null;
}

function sectionOf(parsed: ParsedPost, label: RegExp): Section | undefined {
  return parsed.sections.find((section) => label.test(section.label));
}

function sectionsOf(parsed: ParsedPost, label: RegExp): Section[] {
  return parsed.sections.filter((section) => label.test(section.label));
}

// ── Сборка карточки ──────────────────────────────────────────────────────────

type Recipe = {
  hero?: ShareCardData["hero"];
  chip?: string;
  fact?: string;
  metrics: ShareMetric[];
  lists: ShareNameList[];
  /** Подзаголовок, когда события нет (дата ближайшего старта из текста). */
  subtitle?: string;
};

// Плашка не должна повторять подпись героя (Дмитрий, 07.09.2026: «224 новых
// лица · НОВЫЕ ЛИЦА», «40 юбилеев завтра · ЮБИЛЕИ ЗАВТРА»). Правило: герой
// называет, КОГО посчитали, плашка — по какому поводу постер.
const PLATES: Record<OrganizerPostTemplate, string> = {
  full: "СТАТИСТИКА СТАРТА",
  stats: "ГЕРОИ СТАРТА",
  volunteers: "КОМАНДА СТАРТА",
  newcomers: "НОВЫЕ ЛИЦА",
  milestones: "ПОЗДРАВЛЯЕМ",
  upcoming: "ЗАВТРА НА СТАРТЕ",
  vacancies: "НУЖНЫ ВОЛОНТЁРЫ",
  travelers: "НАШИ В ГОСТЯХ",
};

const FINISHER_FORMS = COUNT_FORMS.finishers;
const VOLUNTEER_FORMS = COUNT_FORMS.volunteers;
const PR_FORMS = COUNT_FORMS.prs;
const NEWCOMER_FORMS = COUNT_FORMS.newcomers;
const LOCATION_FORMS = COUNT_FORMS.locations;
const GUEST_FORMS = ["гость", "гостя", "гостей"] as const;
const JUBILEE_FORMS = ["юбилей", "юбилея", "юбилеев"] as const;
// Под плашкой «НОВЫЕ ЛИЦА» герой считает людей, а не «новые лица» ещё раз.
const PEOPLE_FORMS = ["человек", "человека", "человек"] as const;
const ROLE_FORMS = ["роль", "роли", "ролей"] as const;
const TRIP_FORMS = ["выезд", "выезда", "выездов"] as const;
const VACANCY_FORMS = ["свободная позиция", "свободные позиции", "свободных позиций"] as const;

function countHero(count: number, forms: readonly [string, string, string], suffix = ""): Recipe["hero"] {
  if (count <= 0) {
    return undefined;
  }
  return { value: formatInt(count), caption: `${pluralFormRu(count, forms)}${suffix}` };
}

function countTile(
  metrics: ShareMetric[],
  id: string,
  count: number | null,
  forms: readonly [string, string, string],
  suffix = "",
): void {
  if (count == null || count <= 0) {
    return;
  }
  metrics.push({ id, value: formatInt(count), label: `${pluralFormRu(count, forms)}${suffix}` });
}

function list(lists: ShareNameList[], title: string, items: string[]): void {
  const clean = dedupe(humanize(items));
  if (clean.length > 0) {
    lists.push({ title, items: clean });
  }
}

/** «25-й: А, Б» → [«А · 25-й», «Б · 25-й»]. */
function levelledNames(items: string[]): string[] {
  return items.flatMap((item) => {
    const level = partBefore(item, ":");
    const names = splitNames(partAfter(item, ": "));
    return names.map((name) => `${name} · ${level}`);
  });
}

/** Число финишёров и волонтёров из шапки — общие для сводного поста и «Героев». */
function headerCounts(parsed: ParsedPost): { finishers: number | null; volunteers: number | null } {
  return {
    finishers: parseInt10(sectionOf(parsed, /^Финишёров$/i)?.inline),
    volunteers: parseInt10(sectionOf(parsed, /^Волонтёров$/i)?.inline),
  };
}

function recordChip(parsed: ParsedPost): string | undefined {
  if (parsed.lines.some((line) => /Рекорд посещаемости/i.test(line))) {
    return "Рекорд посещаемости!";
  }
  if (parsed.lines.some((line) => /Рекорд трассы/i.test(line))) {
    return "Новый рекорд трассы!";
  }
  return undefined;
}

/**
 * Строка «+7 новых участников, кто ранее не бегал в системе 5 вёрст» →
 * плитка «+7 / новых участников · впервые в системе». Хвост после запятой
 * длинный — сворачиваем в короткую подпись.
 */
function statTile(raw: string, index: number): ShareMetric | null {
  const match = /^\+(\d+)\s+(.*)$/.exec(raw);
  if (!match) {
    return null;
  }
  const [, count, rest] = match;
  const n = Number(count);
  const head = rest.split(",")[0].trim().replace(" на этой локации", " здесь");
  let label = head;
  if (/не бегал в системе/i.test(rest)) {
    label = `${pluralFormRu(n, NEWCOMER_FORMS)} в системе`;
  } else if (/не бегал в данной локации/i.test(rest)) {
    label = `${pluralFormRu(n, GUEST_FORMS)} · впервые здесь`;
  } else if (/вернулся/i.test(rest)) {
    label = "после паузы больше года";
  } else if (/впервые волонтёрил/i.test(rest)) {
    label = "впервые в роли волонтёра";
  } else if (/новой для себя роли/i.test(rest)) {
    label = "в новой для себя роли";
  }
  return { id: `stat_${index}`, value: `+${count}`, label };
}

function fullRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  const { finishers, volunteers } = headerCounts(parsed);
  countTile(metrics, "volunteers", volunteers, VOLUNTEER_FORMS);
  sectionOf(parsed, /^Статистика мероприятия$/i)?.rawItems.forEach((raw, index) => {
    const tile = statTile(raw, index);
    if (tile) {
      metrics.push(tile);
    }
  });
  list(
    lists,
    "Топ финишей",
    (sectionOf(parsed, /^ТОП финишей$/i)?.items ?? []).map((item) => partBefore(item, " (")),
  );
  list(
    lists,
    "Юбилеи в локации",
    sectionsOf(parsed, /^\d+ (пробежек|волонтёрств) в локации$/i).flatMap((section) => {
      // «100 пробежек в локации» → «Имя · 100-я», волонтёрства — «10-е».
      const count = section.label.split(" ")[0];
      const suffix = /пробежек/i.test(section.label) ? "я" : "е";
      return splitNames(section.inline).map((name) => `${name} · ${count}-${suffix}`);
    }),
  );
  list(
    lists,
    "Клубы",
    sectionsOf(parsed, /^Клуб \d+/i).flatMap((section) =>
      splitNames(section.inline).map((name) => `${name} · клуб ${section.label.split(" ")[1]}`),
    ),
  );
  list(
    lists,
    "Юбилейные пробежки",
    splitNames(sectionOf(parsed, /^Юбилейные пробежки$/i)?.inline).map(stripTrailingParen),
  );
  // Все, кого пост называет по имени, должны быть и на постере (правило
  // Дмитрия): рекордсмены трассы и те, кому остался шаг до юбилея.
  list(
    lists,
    "Рекорд трассы",
    sectionsOf(parsed, /^Рекорд трассы/i).map((section) => partBefore(section.inline)),
  );
  list(
    lists,
    "Шаг до юбилея",
    sectionsOf(parsed, /^1 (пробежка|волонтёрство) до \d+ в локации$/i).flatMap((section) => {
      const target = /до (\d+)/.exec(section.label)?.[1] ?? "";
      return splitNames(section.inline).map((name) => `${name} · до ${target}`);
    }),
  );
  return {
    hero: finishers != null ? countHero(finishers, FINISHER_FORMS) : undefined,
    chip: recordChip(parsed),
    metrics,
    lists,
  };
}

function statsRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  const { finishers, volunteers } = headerCounts(parsed);
  const prs = splitNames(sectionOf(parsed, /^Личные рекорды/i)?.inline);
  const newcomers = splitNames(sectionOf(parsed, /^Первый раз на старте/i)?.inline);
  const guests = (sectionOf(parsed, /^Гости локации/i)?.items ?? []).map((item) => partBefore(item));
  const jubilees = (sectionOf(parsed, /^Юбилеи$/i)?.inline ?? "")
    .split(/;\s*/)
    .map((item) => item.replace(/\.$/, "").trim())
    .filter(Boolean)
    .map((item) => {
      const level = /(\d+-й)/.exec(item)?.[1];
      const name = partBefore(item);
      return level ? `${name} · ${level}` : name;
    });
  countTile(metrics, "volunteers", volunteers, VOLUNTEER_FORMS);
  countTile(metrics, "prs", prs.length, PR_FORMS);
  countTile(metrics, "newcomers", newcomers.length, NEWCOMER_FORMS);
  countTile(metrics, "guests", guests.length, GUEST_FORMS);
  countTile(metrics, "jubilees", jubilees.length, JUBILEE_FORMS);
  list(lists, "Новички", newcomers);
  list(lists, "Личные рекорды", prs);
  list(lists, "Гости", guests);
  list(lists, "Юбилеи", jubilees);
  return {
    hero: finishers != null ? countHero(finishers, FINISHER_FORMS) : undefined,
    metrics,
    lists,
  };
}

function volunteersRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  // Роли — пункты без заголовка: «1. 🪇 Организатор — Имя, Имя».
  const roles = parsed.sections
    .filter((section) => !section.label)
    .flatMap((section) => section.items)
    .filter((item) => item.includes(" — "))
    .map((item) => ({ role: partBefore(item), names: splitNames(partAfter(item)) }));
  const everyone = dedupe(roles.flatMap((role) => role.names));
  countTile(metrics, "roles", roles.length, ROLE_FORMS);
  roles.forEach((role, index) => {
    if (role.names.length > 0) {
      metrics.push({
        id: `role_${index}`,
        value: formatInt(role.names.length),
        label: role.role,
        keepLabelCase: true,
      });
    }
  });
  // Список на роль — как в самом посте (формат Мещерского): роль стоит
  // заголовком строки, за ней плашки имён. Постер печатает всех.
  roles.forEach((role) => list(lists, role.role, role.names));
  return { hero: countHero(everyone.length, VOLUNTEER_FORMS), metrics, lists };
}

function newcomersRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  const blocks: { label: RegExp; title: string; tile: string }[] = [
    { label: /^Первый финиш$/i, title: "Первый финиш", tile: "первый финиш" },
    { label: /^Первое волонтёрство$/i, title: "Первое волонтёрство", tile: "первое волонтёрство" },
    { label: /^Впервые на нашей локации$/i, title: "Впервые у нас", tile: "впервые здесь" },
  ];
  let total = 0;
  blocks.forEach((block, index) => {
    const names = dedupe(sectionOf(parsed, block.label)?.items ?? []);
    if (names.length === 0) {
      return;
    }
    total += names.length;
    metrics.push({ id: `block_${index}`, value: formatInt(names.length), label: block.tile });
    list(lists, block.title, names);
  });
  return { hero: countHero(total, PEOPLE_FORMS), metrics, lists };
}

function milestonesRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  let total = 0;
  sectionsOf(parsed, /^Юбилейные (финиши|волонтёрства)/i).forEach((section, index) => {
    const names = levelledNames(section.items);
    if (names.length === 0) {
      return;
    }
    total += names.length;
    const isRun = /финиши/i.test(section.label);
    const where = /систем/i.test(section.label) ? "в системе" : "здесь";
    const forms = isRun
      ? (["юбилейный финиш", "юбилейных финиша", "юбилейных финишей"] as const)
      : (["юбилейное волонтёрство", "юбилейных волонтёрства", "юбилейных волонтёрств"] as const);
    countTile(metrics, `block_${index}`, names.length, forms, ` ${where}`);
    list(lists, `${isRun ? "Финиши" : "Волонтёрства"} ${where}`, names);
  });
  return { hero: countHero(total, JUBILEE_FORMS), metrics, lists };
}

function upcomingRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  let total = 0;
  sectionsOf(parsed, /^(Пробежки|Волонтёрства) (здесь|в системе)$/i).forEach((section, index) => {
    const names = levelledNames(section.items);
    if (names.length === 0) {
      return;
    }
    total += names.length;
    // «15 юбилеев · пробежки здесь»: голое название раздела после числа
    // читалось как «15 пробежки здесь».
    metrics.push({
      id: `block_${index}`,
      value: formatInt(names.length),
      label: `${pluralFormRu(names.length, JUBILEE_FORMS)} · ${section.label.toLowerCase()}`,
    });
    list(lists, section.label, names);
  });
  // Плашка уже говорит «завтра» — подпись героя без него, иначе дубль.
  return { hero: countHero(total, JUBILEE_FORMS), metrics, lists, subtitle: "Ближайший старт" };
}

function vacanciesRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  const open = dedupe(parsed.lines.filter((line) => line.startsWith("❗")).map(itemText));
  const filled = dedupe(parsed.lines.filter((line) => line.startsWith("✅")).map(itemText));
  countTile(metrics, "filled", filled.length, ["позиция занята", "позиции заняты", "позиций заняты"]);
  countTile(metrics, "open", open.length, VACANCY_FORMS);
  list(lists, "Нужны", open);
  list(lists, "Уже в строю", filled);
  const date = /на (\d{2}\.\d{2}\.\d{4})/.exec(parsed.title)?.[1];
  const signup = parsed.lines.find((line) => /^✍️?\s*Запись:/.test(line));
  const signupUrl = signup ? partAfter(signup, "Запись:").replace(/^https?:\/\//, "") : "";
  // Ссылка на запись — в подзаголовок: строкой-фактом под списками она в
  // широком формате наезжала на бренд-футер.
  const subtitle = [date ? `Ближайший старт · ${date}` : "Ближайший старт", signupUrl ? `запись: ${signupUrl}` : null]
    .filter(Boolean)
    .join(" · ");
  return {
    hero:
      open.length > 0
        ? countHero(open.length, VACANCY_FORMS)
        : filled.length > 0
          ? {
              value: "Команда в сборе",
              caption: `${formatInt(filled.length)} ${pluralFormRu(filled.length, ["позиция", "позиции", "позиций"])} закрыты`,
            }
          : undefined,
    metrics,
    lists,
    subtitle,
  };
}

function travelersRecipe(parsed: ParsedPost): Recipe {
  const metrics: ShareMetric[] = [];
  const lists: ShareNameList[] = [];
  const trips = parsed.sections
    .flatMap((section) => section.items)
    .filter((item) => item.includes(" — "))
    .map((item) => ({
      name: stripTrailingParen(partBefore(item)),
      away: partBefore(partAfter(item)),
    }))
    .filter((trip) => trip.name && trip.away);
  const awayLocations = dedupe(trips.map((trip) => stripTrailingParen(trip.away)));
  countTile(metrics, "locations", awayLocations.length, LOCATION_FORMS);
  list(
    lists,
    "В гостях",
    trips.map((trip) => `${trip.name} — ${stripTrailingParen(trip.away)}`),
  );
  return { hero: countHero(trips.length, TRIP_FORMS), metrics, lists };
}

const RECIPES: Record<OrganizerPostTemplate, (parsed: ParsedPost) => Recipe> = {
  full: fullRecipe,
  stats: statsRecipe,
  volunteers: volunteersRecipe,
  newcomers: newcomersRecipe,
  milestones: milestonesRecipe,
  upcoming: upcomingRecipe,
  vacancies: vacanciesRecipe,
  travelers: travelersRecipe,
};

function eventSubtitle(event: OrganizerEventDateItem): string {
  return [
    event.event_number != null ? `Старт №${formatInt(event.event_number)}` : null,
    formatDate(event.event_date),
    platformCodeLabel(event.platform_code),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Сюжет постера по тексту поста (уже отредактированному организатором). */
export function organizerPostSubject(text: string, context: OrganizerPosterContext): ShareSubject {
  const parsed = parsePost(text);
  const recipe = RECIPES[context.template](parsed);
  const title = parsed.location || context.locationName || context.slug;
  // Пост целиком переписан и цифр в нём не нашлось — хотя бы финишёров
  // события покажем, иначе постер выходит пустым.
  const hero =
    recipe.hero ??
    (context.event?.finishers_count ? countHero(context.event.finishers_count, FINISHER_FORMS) : undefined);
  // Порядок блоков на постере — от коротких к длинным по числу людей
  // (правило Дмитрия 07.09.2026, для всех тем): организатор одной строкой
  // сверху, восемь маршалов — в самый низ. При равном числе людей остаётся
  // порядок поста (sort стабилен): среди ролей по одному человеку
  // организатор всё равно первый.
  const lists = [...recipe.lists].sort((a, b) => a.items.length - b.items.length);
  const data: ShareCardData = {
    audience: "location",
    title,
    subtitle: context.event ? eventSubtitle(context.event) : recipe.subtitle,
    plate: PLATES[context.template],
    hero,
    chip: recipe.chip,
    metrics: recipe.metrics,
    lists,
    fact: recipe.fact,
  };
  const datePart = context.event?.event_date ?? new Date().toISOString().slice(0, 10);
  return {
    kind: "organizer_post",
    data,
    fileName: `run5k-${context.slug}-${context.template}-${datePart}`,
    // Лента 4:5, а не широкий: именным спискам нужна высота, в 1200×630
    // двадцать волонтёров печатаются 13-пиксельным кеглем.
    defaultFormat: "feed",
  };
}
