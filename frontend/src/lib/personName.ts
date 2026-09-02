/**
 * «Дмитрий ПОПОВ» → «ПОПОВ Дмитрий»: в рейтингах имя выводится с фамилии,
 * чтобы столбец «Участник» читался и сортировался как список по фамилиям
 * (правка Дмитрия 03.09.2026).
 *
 * Все беговые системы (5 вёрст, parkrun, S95, RunPark) и профили сайта хранят
 * имя как «Имя Фамилия», поэтому фамилия — последнее слово. Не трогаем:
 * — одно слово (логин вроде «vika07101999» или «Неизвестный»);
 * — приватный стиль «Иван П.» — инициал фамилией вперёд читается хуже.
 */
export function surnameFirst(name: string | null | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length < 2) {
    return words.join(" ");
  }
  const last = words[words.length - 1];
  if (/^\p{L}\.?$/u.test(last)) {
    return words.join(" ");
  }
  return [last, ...words.slice(0, -1)].join(" ");
}
