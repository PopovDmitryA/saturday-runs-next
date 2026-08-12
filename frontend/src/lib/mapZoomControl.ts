import L from "leaflet";

/**
 * Зум-кнопки в правом верхнем углу карты вместо левого (дефолт Leaflet).
 * Телефон держат правой рукой, и до «+/−» у левого края приходится тянуться
 * через весь экран. По высоте положение то же, что было слева.
 *
 * Кнопка фуллскрина живёт в том же углу — её обходит CSS-правило для
 * `.location-map-shell:has(.location-map-fullscreen-btn)` (index.css): там,
 * где кнопка есть, зум сдвигается левее неё.
 *
 * Карта при этом создаётся с `zoomControl: false` — иначе Leaflet добавит свой
 * контрол слева и кнопок станет две.
 */
export function addZoomControl(map: L.Map) {
  L.control.zoom({ position: "topright" }).addTo(map);
}
