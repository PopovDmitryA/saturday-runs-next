import { platformLogoSrc } from "../lib/platformLogos";
import { platformCodeLabel } from "../lib/format";

type PlatformProfileLogoProps = {
  code: string;
};

export function PlatformProfileLogo({ code }: PlatformProfileLogoProps) {
  const src = platformLogoSrc(code);
  const label = platformCodeLabel(code);

  if (!src) {
    return <span className="about-link-platform-fallback">{label}</span>;
  }

  return (
    <img
      src={src}
      alt=""
      className="about-link-platform-logo"
      width={40}
      height={40}
      loading="lazy"
      decoding="async"
    />
  );
}
