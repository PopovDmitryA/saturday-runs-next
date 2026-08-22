// Глобальная шторка «Поделиться»: провайдер монтируется один раз в App,
// любая кнопка на сайте открывает шторку через useShareSheet().open(...) —
// без навигации и sessionStorage-эстафет старого мастера.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { trackShareOpen } from "./analytics";
import { ShareSheet } from "./ShareSheet";
import type { ShareEntryPoint, ShareSubject } from "./types";

export type ShareSheetOpenArgs = {
  subject: ShareSubject;
  entry: ShareEntryPoint;
};

type ShareSheetApi = {
  open: (args: ShareSheetOpenArgs) => void;
};

const ShareSheetContext = createContext<ShareSheetApi | null>(null);

export function ShareSheetProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<ShareSheetOpenArgs | null>(null);

  const open = useCallback((args: ShareSheetOpenArgs) => {
    trackShareOpen(args.subject.kind, args.entry);
    setCurrent(args);
  }, []);

  const api = useMemo(() => ({ open }), [open]);

  return (
    <ShareSheetContext.Provider value={api}>
      {children}
      {current ? <ShareSheet subject={current.subject} onClose={() => setCurrent(null)} /> : null}
    </ShareSheetContext.Provider>
  );
}

export function useShareSheet(): ShareSheetApi {
  const api = useContext(ShareSheetContext);
  if (api === null) {
    throw new Error("useShareSheet вызван вне ShareSheetProvider");
  }
  return api;
}

/** Мягкий вариант для компонентов, живущих и вне провайдера (демо-превью). */
export function useOptionalShareSheet(): ShareSheetApi | null {
  return useContext(ShareSheetContext);
}
