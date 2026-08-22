/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" — пилот АБ-теста главной включён (раскладка 50/50). См. lib/abTest.ts. */
  readonly VITE_AB_HOME_ACTIVE?: string;
}

declare module "*.png" {
  const src: string;
  export default src;
}
