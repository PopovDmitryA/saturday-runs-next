import L from "leaflet";

/**
 * Зум-кнопки у правого края карты, ниже середины (а не в углу, как по дефолту
 * Leaflet). Телефон держат правой рукой: до «+/−» в любом верхнем углу нужно
 * перехватывать аппарат, а на этой высоте они попадают ровно под большой палец
 * (просьба Дмитрия 22.08.2026).
 *
 * Кнопка фуллскрина остаётся в правом верхнем углу — зум висит под ней, по той
 * же правой кромке. Само положение задаёт CSS по классу `map-zoom-thumb`
 * (index.css), поэтому Leaflet'у хватает обычного `topright`.
 *
 * Карта при этом создаётся с `zoomControl: false` — иначе Leaflet добавит свой
 * контрол слева и кнопок станет две.
 */
export function addZoomControl(map: L.Map) {
  const control = L.control.zoom({ position: "topright" });
  control.addTo(map);
  control.getContainer()?.classList.add("map-zoom-thumb");
}
