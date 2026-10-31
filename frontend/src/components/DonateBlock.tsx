import { useState } from "react";
import { StatHintTooltip } from "./StatHintTooltip";
import {
  formatDayMonth,
  formatRub,
  monthName,
  monthPrepositional,
  monthTitle,
  pluralTransfers,
  useFundingMonth,
  type FundingMonth,
} from "./donateFundingApi";
import "./DonateBlock.css";

// Реквизиты для поддержки проекта.
const DONATE_QR_SRC: string | null = "/donate-qr.png";

const DONATE_CARDS = [
  { bank: "Сбер", code: "sber", mark: "С", number: "2202 2032 1013 9616" },
  { bank: "Т-Банк", code: "tbank", mark: "Т", number: "2200 7001 6683 5594" },
] as const;

// Реквизиты общие, а текст — разный: на «О проекте» рассказ про проект в целом
// и полный месяц (шкала, расходы, кто поддержал, стрелки на прошлые месяцы),
// в бэклоге — одна строка текущего месяца со ссылкой на «О проекте».
// Цифры и имена приходят отдельным запросом (/api/funding/month): в
// пререндер /about и в кэш страниц они не попадают. Пока ответа нет или показ
// выключен в админке — блок как раньше, только реквизиты.
type DonateVariant = "about" | "backlog";

type DonateBlockProps = {
  variant?: DonateVariant;
};

const ABOUT_DONATE_HREF = "/about#donate";

function DonateIcon() {
  return (
    <span className="donate-block-icon" aria-hidden="true">
      <svg viewBox="0 0 16 16">
        <path d="M8 1.5 9.4 6.1 14 7.5 9.4 8.9 8 13.5 6.6 8.9 2 7.5 6.6 6.1Z" />
      </svg>
    </span>
  );
}

function DonateRequisites({ withNames }: { withNames: boolean }) {
  return (
    <div className="donate-block-card">
      <div className="donate-block-card-main">
        <div className="donate-block-cards">
          {DONATE_CARDS.map((card) => (
            <div className="donate-block-card-row" key={card.code}>
              <span className={`donate-block-mark ${card.code}`} aria-hidden="true">
                {card.mark}
              </span>
              <span className="donate-block-card-info">
                <span className="donate-block-card-bank">{card.bank}</span>
                <b className="donate-block-card-number num">{card.number}</b>
              </span>
            </div>
          ))}
        </div>
        <div className="donate-block-qr-block">
          {DONATE_QR_SRC ? (
            <img
              className="donate-block-qr"
              src={DONATE_QR_SRC}
              alt="QR-код для перевода по СБП"
              width={104}
              height={104}
            />
          ) : (
            <div className="donate-block-qr-empty" aria-hidden="true">
              QR
            </div>
          )}
          <span className="donate-block-qr-caption">Перевод по СБП</span>
        </div>
      </div>
      {withNames && (
        <div className="donate-block-names-note">
          <span>В списке поддержавших будет ваше имя, как в переводе, например «Иван П.».</span>
          <span>Хотите анонимно — напишите об этом в комментарии к переводу.</span>
          <span>Перевод появится здесь в течение 3 суток.</span>
        </div>
      )}
    </div>
  );
}

function FundingMeter({ data, compact = false }: { data: FundingMonth; compact?: boolean }) {
  const cost = data.cost_total_rub;
  const donated = data.donated_total_rub;
  // Месяц без расходов (все статьи по 0 ₽) — пустая шкала, а не полная.
  const percent = cost > 0 ? Math.floor((donated * 100) / cost) : 0;
  const width = Math.min(percent, 100);
  return (
    <div
      className={compact ? "donate-meter compact" : "donate-meter"}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={width}
      aria-label="Сколько расходов месяца покрыли переводы"
    >
      <div className="donate-meter-fill" style={{ width: `${width}%` }} />
    </div>
  );
}

// lowercase — после «Октябрь:» в строке бэклога.
function fundingSummary(data: FundingMonth, lowercase = false) {
  const cost = data.cost_total_rub;
  const donated = data.donated_total_rub;
  if (cost === 0) {
    return (
      <>
        {lowercase ? "поддержали" : "Поддержали"} <b>{formatRub(donated)}</b>
      </>
    );
  }
  if (donated > cost) {
    return (
      <>
        {lowercase ? "покрыт" : "Покрыт"} с запасом: <b>{formatRub(donated)}</b> при расходах {formatRub(cost)}
      </>
    );
  }
  const percent = Math.floor((donated * 100) / cost);
  return (
    <>
      {lowercase ? "поддержали" : "Поддержали"} <b>{formatRub(donated)}</b> из {formatRub(cost)} · {percent}%
    </>
  );
}

// Будущий месяц с планом расходов: открыт, чтобы был виден план, но
// переводов в нём ещё нет — они идут в текущий месяц.
function isFutureMonth(data: FundingMonth): boolean {
  return Boolean(data.month && data.current_month && data.month > data.current_month);
}

function FutureMonthNote({ data }: { data: FundingMonth }) {
  return (
    <p className="donate-future-note">
      Месяц ещё не начался — все переводы сейчас идут в {monthName(data.current_month ?? "").toLowerCase()}.
    </p>
  );
}

function FundingMonthNav({
  data,
  loading,
  onShift,
}: {
  data: FundingMonth;
  loading: boolean;
  onShift: (delta: number) => void;
}) {
  const month = data.month ?? "";
  const atFirst = !data.first_month || month <= data.first_month;
  const last = data.last_month ?? data.current_month;
  const atLast = !last || month >= last;
  return (
    <div className="donate-month-nav">
      <button
        type="button"
        className="donate-month-step"
        aria-label="Предыдущий месяц"
        disabled={atFirst || loading}
        onClick={() => onShift(-1)}
      >
        ‹
      </button>
      <span className="donate-month-name" aria-live="polite">
        {monthTitle(month)}
        {isFutureMonth(data) && <span className="donate-month-badge">план</span>}
      </span>
      <button
        type="button"
        className="donate-month-step"
        aria-label="Следующий месяц"
        disabled={atLast || loading}
        onClick={() => onShift(1)}
      >
        ›
      </button>
    </div>
  );
}

function FundingDetails({ data }: { data: FundingMonth }) {
  const month = data.month ?? "";
  const future = isFutureMonth(data);
  return (
    <div className="donate-details" id="donate-details">
      <div className="donate-details-col">
        <h3>{future ? "План расходов" : "Куда уходят деньги"}</h3>
        <div className="donate-lines num">
          {data.costs.map((cost, index) => (
            <div className="donate-line" key={`${index}-${cost.title}`}>
              <span>
                {cost.title}
                {cost.hint && (
                  <span className="donate-line-hint">
                    <StatHintTooltip text={cost.hint}>
                      <span aria-label={`Зачем: ${cost.title}`}>ⓘ</span>
                    </StatHintTooltip>
                  </span>
                )}
              </span>
              <span className="donate-line-amount">{formatRub(cost.amount_rub)}</span>
            </div>
          ))}
          <div className="donate-line donate-line-total">
            <span>Итого</span>
            <span className="donate-line-amount">{formatRub(data.cost_total_rub)}</span>
          </div>
        </div>
      </div>
      <div className="donate-details-col">
        <h3>Кто поддержал в {monthPrepositional(month)}</h3>
        {future ? (
          <p className="donate-empty">Месяц ещё не начался.</p>
        ) : data.donations.length === 0 ? (
          <p className="donate-empty">Переводов пока не было.</p>
        ) : (
          <div className="donate-lines num">
            {data.donations.map((donation, index) => (
              <div className="donate-line donate-line-donation" key={`${donation.received_on}-${index}`}>
                <span className="donate-line-date">{formatDayMonth(donation.received_on)}</span>
                <span className={donation.name ? undefined : "donate-line-anon"}>
                  {donation.name ?? "Аноним"}
                </span>
                <span className="donate-line-amount">{formatRub(donation.amount_rub)}</span>
              </div>
            ))}
            <div className="donate-line donate-line-total">
              <span>Всего</span>
              <span className="donate-line-amount">{formatRub(data.donated_total_rub)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AboutDonate() {
  const { data, loading, shift } = useFundingMonth();
  const [detailsOpen, setDetailsOpen] = useState(true);
  const visible = Boolean(data?.visible && data.month);

  // Компактно: на компьютере слева месяц, шкала и под ними оба списка,
  // справа реквизиты на всю высоту; на телефоне реквизиты — между шкалой и
  // списками, чтобы перевести можно было, не листая списки.
  return (
    <div className="donate-block">
      <div className="donate-block-top">
        <div className="donate-block-copy">
          <div className="donate-block-head">
            <DonateIcon />
            <h2>Поддержать проект</h2>
          </div>
          {visible && data ? (
            <>
              <p>
                run5k.run — личный проект без рекламы и платных функций. Серверы, сервисы и
                AI-агенты, на которых сайт разрабатывается, оплачиваются из моих денег и ваших
                переводов.
              </p>
              <div className="donate-month-row">
                <FundingMonthNav data={data} loading={loading} onShift={shift} />
                {isFutureMonth(data) ? (
                  <p className="donate-summary num">
                    План расходов: <b>{formatRub(data.cost_total_rub)}</b>
                  </p>
                ) : (
                  <p className="donate-summary num">
                    {fundingSummary(data)}
                    <span className="donate-meta"> · {pluralTransfers(data.donations.length)}</span>
                  </p>
                )}
              </div>
              {isFutureMonth(data) ? <FutureMonthNote data={data} /> : <FundingMeter data={data} />}
            </>
          ) : (
            <>
              <p>
                run5k.run — личный проект: без рекламы и платных функций, все разделы открыты
                всем.
              </p>
              <p>
                Постоянные расходы у него всё же есть: сервер, хостинг и AI-агенты, на которых
                это всё разрабатывается. Если сайт оказался полезен и захотелось поддержать — вот
                реквизиты.
              </p>
            </>
          )}
        </div>
        <DonateRequisites withNames={visible} />
        {visible && data && (
          <div className="donate-block-more">
            <button
              type="button"
              className="donate-details-toggle"
              aria-expanded={detailsOpen}
              aria-controls="donate-details"
              onClick={() => setDetailsOpen((open) => !open)}
            >
              {detailsOpen ? "▾" : "▸"} Куда уходят деньги и кто поддержал
            </button>
            {detailsOpen && <FundingDetails data={data} />}
          </div>
        )}
      </div>
    </div>
  );
}

function BacklogDonate() {
  const { data } = useFundingMonth();
  const visible = Boolean(data?.visible && data.month);

  return (
    <div className="donate-block">
      <div className="donate-block-top">
        <div className="donate-block-copy">
          <div className="donate-block-head">
            <DonateIcon />
            <h2>Поддержать проект</h2>
          </div>
          <p>Поддержание и развитие проекта требуют денег. Сайт живёт на мои деньги и ваши переводы.</p>
          {visible && data && (
            <div className="donate-backlog-line num">
              <span>
                {monthName(data.month ?? "")}: {fundingSummary(data, true)}
              </span>
              <FundingMeter data={data} compact />
              <a href={ABOUT_DONATE_HREF}>Подробнее →</a>
            </div>
          )}
        </div>
        <DonateRequisites withNames={false} />
      </div>
    </div>
  );
}

export function DonateBlock({ variant = "about" }: DonateBlockProps) {
  return variant === "backlog" ? <BacklogDonate /> : <AboutDonate />;
}
