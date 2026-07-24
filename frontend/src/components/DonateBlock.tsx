import "./DonateBlock.css";

// Реквизиты для поддержки проекта.
const DONATE_QR_SRC: string | null = "/donate-qr.png";

const DONATE_CARDS = [
  { bank: "Сбер", code: "sber", mark: "С", number: "2202 2032 1013 9616" },
  { bank: "Т-Банк", code: "tbank", mark: "Т", number: "2200 7001 6683 5594" },
] as const;

// Реквизиты общие, а текст — разный: на «О проекте» рассказ про проект в целом,
// в бэклоге упор на то, что донаты ускоряют выкатку запрошенных фич.
type DonateVariant = "about" | "backlog";

type DonateBlockProps = {
  variant?: DonateVariant;
};

function DonateCopy({ variant }: { variant: DonateVariant }) {
  if (variant === "backlog") {
    return (
      <p>
        Поддержание и развитие проекта требуют денег. Ваши донаты помогают сайту жить и
        реализовывать новые фичи.
      </p>
    );
  }
  return (
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
  );
}

export function DonateBlock({ variant = "about" }: DonateBlockProps) {
  return (
    <div className="donate-block">
      <div className="donate-block-copy">
        <div className="donate-block-head">
          <span className="donate-block-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16">
              <path d="M8 1.5 9.4 6.1 14 7.5 9.4 8.9 8 13.5 6.6 8.9 2 7.5 6.6 6.1Z" />
            </svg>
          </span>
          <div>
            <h2>Поддержать проект</h2>
          </div>
        </div>
        <DonateCopy variant={variant} />
      </div>
      <div className="donate-block-card">
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
    </div>
  );
}
