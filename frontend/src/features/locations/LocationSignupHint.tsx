import { SignupCta } from "../../components/SystemSignupLink";
import type { LocationPagePlatform } from "../../lib/api";
import { platformCodeLabel } from "../../lib/format";
import { isSignupPlatform, s95DomainFromUrl } from "../../lib/systemSignup";
import { useOptionalUser } from "../../lib/useOptionalUser";
import "../portal/systemRegistration.css";

/**
 * «У вас не привязан профиль С95 — зарегистрироваться и привязать» — в шапке
 * локации (решения Дмитрия 25.10.2026). Без вопросов: профиль не привязан —
 * считаем, что человек в системе не зарегистрирован.
 *
 * Только для действующей системы локации и только тем, у кого её профиля нет;
 * аноним видит строку всегда — у него может не быть профиля вовсе. Строка
 * живёт на клиенте: публичную часть страницы кэширует сервер и пререндерит
 * роботам, личного в ней быть не должно.
 *
 * С95 и RunPark пускают на старт со штрихкодом другой системы. Тому, у кого
 * такая система привязана, сначала говорим об этом: регистрация в С95 после
 * стартов с чужим штрихкодом заводит второй профиль, а склеивает их только
 * сама С95 по обратной связи.
 */

// Системы, которые принимают штрихкод другой системы (правила С95, форма RunPark).
const ACCEPTS_FOREIGN_BARCODE = new Set(["s95", "runpark"]);

export function LocationSignupHint({
  platforms,
  isPaused,
}: {
  platforms: LocationPagePlatform[];
  isPaused: boolean;
}) {
  const user = useOptionalUser();
  // Сессия ещё проверяется — не мигаем призывом перед тем, у кого профиль есть.
  if (user === undefined || isPaused) {
    return null;
  }
  const active = platforms.find((platform) => platform.is_active && isSignupPlatform(platform.platform_code));
  if (!active || !isSignupPlatform(active.platform_code)) {
    return null;
  }
  const code = active.platform_code;
  const linked = user?.linked_platform_codes ?? [];
  if (linked.includes(code)) {
    return null;
  }
  const s95Domain = code === "s95" ? s95DomainFromUrl(active.url) : null;
  const ownBarcode = ACCEPTS_FOREIGN_BARCODE.has(code)
    ? linked.find((other) => other !== code)
    : undefined;
  const label = platformCodeLabel(code);
  const cta = (
    <SignupCta code={code} place="location" s95Domain={s95Domain}>
      зарегистрироваться и привязать
    </SignupCta>
  );
  // Гостю «у вас не привязан» не скажешь — его профилей мы не знаем.
  if (!user) {
    return (
      <p className="signup-hint loc-signup-hint">
        Здесь бегают в {label} — {cta}.
      </p>
    );
  }
  return (
    <p className="signup-hint loc-signup-hint">
      У вас не привязан профиль {label} — {cta}.
      {ownBarcode && ` Бегать здесь можно и со своим штрихкодом ${platformCodeLabel(ownBarcode)}.`}
    </p>
  );
}
