// Сопоставление названий регионов из БД (сокращённые: «Московская»,
// «Краснодарский», «Татарстан») с полными названиями в GeoJSON
// («Московская область», «Краснодарский край», «Татарстан»).

const TYPE_WORDS = [
  "автономный округ",
  "автономная область",
  "область",
  "край",
  "республика",
];

// Регионы, чьи прилагательные-формы не совпадают между БД и GeoJSON.
const ALIASES: Record<string, string> = {
  удмуртская: "удмуртия",
  чеченская: "чечня",
  чувашская: "чувашия",
};

export function regionKey(raw: string | null | undefined): string {
  if (!raw) {
    return "";
  }
  let value = raw.toLowerCase().trim();
  value = value.replace(/[—–]/g, "-");
  for (const word of TYPE_WORDS) {
    value = value.replace(word, " ");
  }
  value = value.replace(/[^\p{L}\p{N}\s()-]/gu, " ");
  value = value.replace(/\s+/g, " ").trim();
  return ALIASES[value] ?? value;
}
