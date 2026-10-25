import type { ReactNode } from "react";
import { trackSignupClick } from "../lib/abTest";
import { platformCodeLabel } from "../lib/format";
import { registrationPageHref, signupUrl, type SignupPlatformCode } from "../lib/systemSignup";

/**
 * Ссылка на форму регистрации беговой системы. Живёт только на нашей
 * /registration: остальные страницы ведут туда через SignupCta.
 *
 * Открывается в новой вкладке: после регистрации человек возвращается к нам
 * привязать профиль, и вкладка с подсказкой должна остаться. rel — без
 * noreferrer, в отличие от остальных внешних ссылок сайта: Метрика системы
 * должна увидеть, что человек пришёл с run5k.run (наш Referrer-Policy отдаёт
 * ей только домен, без пути).
 */
export function SystemSignupLink({
  code,
  place,
  s95Domain,
  className,
  onOpen,
  children,
}: {
  code: SignupPlatformCode;
  place: string;
  // Сайт С95 страны локации: от него зависит список домашних забегов в форме.
  s95Domain?: string | null;
  className?: string;
  // Форма системы открылась — страница начинает ждать номер (signupIntent).
  onOpen?: () => void;
  children?: ReactNode;
}) {
  return (
    <a
      className={className}
      href={signupUrl(code, place, { s95Domain })}
      target="_blank"
      rel="noopener"
      onClick={() => {
        trackSignupClick(code, place);
        onOpen?.();
      }}
    >
      {children ?? `Зарегистрироваться в ${platformCodeLabel(code)} ↗`}
    </a>
  );
}

/**
 * Призыв «Зарегистрироваться» на страницах сайта. Всегда ведёт на нашу
 * /registration, где эта система основная, — и только оттуда человек уходит
 * на сайт системы (решение Дмитрия 25.10.2026). Там же всё, что нужно знать
 * до формы: подтвердить почту С95 за 3 часа, что parkrun в России не
 * проводится, как потом привязать профиль.
 */
export function SignupCta({
  code,
  place,
  s95Domain,
  withName = false,
  children,
}: {
  code: SignupPlatformCode;
  place: string;
  // Сайт С95 страны локации — станет адресом кнопки на странице регистрации.
  s95Domain?: string | null;
  // «Зарегистрироваться в С95 →» вместо «Зарегистрироваться →» — там, где
  // система не названа рядом (список систем в онбординге).
  withName?: boolean;
  children?: ReactNode;
}) {
  return (
    <a href={registrationPageHref(code, place, s95Domain)}>
      {children ?? `Зарегистрироваться${withName ? ` в ${platformCodeLabel(code)}` : ""} →`}
    </a>
  );
}
