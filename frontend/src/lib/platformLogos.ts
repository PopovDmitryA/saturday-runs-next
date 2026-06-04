/** Square logos for platform profile links (sourced from official sites). */
export const PLATFORM_LOGO_SRC: Record<string, string> = {
  five_verst: "/platform-logos/five-verst.png",
  s95: "/platform-logos/s95.png",
  parkrun: "/platform-logos/parkrun.png",
};

export function platformLogoSrc(code: string): string | undefined {
  return PLATFORM_LOGO_SRC[code];
}
