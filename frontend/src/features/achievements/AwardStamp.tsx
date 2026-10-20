import { useId } from "react";
import type { UserAward } from "../../lib/api";

/**
 * Значок особой награды — почтовая марка: зубцы перфорации, гравированное
 * поле, номер выпуска и гашение в углу. Выбор Дмитрия 21.10.2026 из десяти
 * вариантов: среди круглых золотых медалей марка сразу читается как другой
 * предмет, а награды складываются в серию — у каждой своё поле (tone), номер
 * (emblem: «1000», «10 000») или символ (icon) и подпись внизу (caption).
 * Габарит — как у медали 3.5rem: строки подписей в ряду витрины не скачут.
 */

// Поле марки: верх и низ градиента краски. Новый цвет — сюда и в проверку
// tone в backend/tests/test_user_awards.py.
const STAMP_TONES: Record<string, readonly [string, string]> = {
  carmine: ["#d9493c", "#951f19"],
  ultramarine: ["#4a6fe0", "#1f2f8c"],
  emerald: ["#2fa475", "#12603f"],
};

export function AwardStamp({ award }: { award: UserAward }) {
  // id градиентов и маски уникальны на странице: наград бывает несколько, а
  // url(#…) из чужой марки перекрасил бы эту. Двоеточия useId в url() не
  // годятся — оставляем только буквы и цифры.
  const uid = `stamp${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [inkTop, inkBottom] = STAMP_TONES[award.tone ?? ""] ?? STAMP_TONES.carmine;
  const emblem = award.emblem?.trim() || null;
  const longEmblem = (emblem?.length ?? 0) > 4;

  return (
    <span className="award-stamp" aria-hidden="true">
      <svg className="award-stamp-svg" viewBox="0 0 50 60" width="50" height="60">
        <defs>
          <linearGradient id={`${uid}-paper`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fffbf2" />
            <stop offset="1" stopColor="#efe4cc" />
          </linearGradient>
          <linearGradient id={`${uid}-ink`} x1="0" y1="0" x2="0.35" y2="1">
            <stop offset="0" stopColor={inkTop} />
            <stop offset="1" stopColor={inkBottom} />
          </linearGradient>
          <pattern id={`${uid}-engr`} width="1.25" height="1.25" patternUnits="userSpaceOnUse">
            <rect width="1.25" height="0.42" fill="#000" opacity="0.13" />
          </pattern>
          {/* Перфорация: круглые вырубки по краю — пунктирная обводка маской */}
          <mask id={`${uid}-perf`} maskUnits="userSpaceOnUse" x="-3" y="-3" width="56" height="66">
            <rect width="50" height="60" fill="#fff" />
            <rect
              width="50"
              height="60"
              fill="none"
              stroke="#000"
              strokeWidth="3.3"
              strokeLinecap="round"
              strokeDasharray=".01 4.99"
              strokeDashoffset="-2.495"
            />
          </mask>
        </defs>
        <g mask={`url(#${uid}-perf)`}>
          <rect width="50" height="60" fill={`url(#${uid}-paper)`} />
          <rect x="5" y="5" width="40" height="50" fill={`url(#${uid}-ink)`} />
          <rect x="5" y="5" width="40" height="50" fill={`url(#${uid}-engr)`} />
          <rect
            x="7.2"
            y="7.2"
            width="35.6"
            height="45.6"
            fill="none"
            stroke="#fff3df"
            strokeOpacity=".6"
            strokeWidth=".55"
          />
          {emblem ? (
            <>
              <text x="25" y="18.2" textAnchor="middle" className="award-stamp-no">
                №
              </text>
              <text
                x="25"
                y={longEmblem ? 34.4 : 35.6}
                textAnchor="middle"
                className={`award-stamp-num${longEmblem ? " award-stamp-num-long" : ""}`}
                textLength={longEmblem ? 34 : 31}
                lengthAdjust="spacingAndGlyphs"
              >
                {emblem}
              </text>
            </>
          ) : (
            <text x="25" y="33.5" textAnchor="middle" className="award-stamp-icon">
              {award.icon}
            </text>
          )}
          <path d="M16 40.2h18" stroke="#fff3df" strokeOpacity=".55" strokeWidth=".5" />
          {award.caption && (
            <text x="25" y="47" textAnchor="middle" className="award-stamp-cap">
              {award.caption}
            </text>
          )}
          {/* Гашение: штемпель с волнами в правом верхнем углу */}
          <g className="award-stamp-postmark" fill="none">
            <circle cx="43.5" cy="8.5" r="8.6" />
            <circle cx="43.5" cy="8.5" r="6.4" strokeDasharray="1.1 .9" />
            <path d="M-1 3.6q2.5-1.5 5 0t5 0t5 0t5 0t5 0t5 0M-1 7.1q2.5-1.5 5 0t5 0t5 0t5 0t5 0t5 0M-1 10.6q2.5-1.5 5 0t5 0t5 0t5 0t5 0t5 0" />
          </g>
        </g>
      </svg>
    </span>
  );
}
