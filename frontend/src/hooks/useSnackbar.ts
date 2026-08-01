import { useState, type ReactNode } from "react";

export type SnackbarState = {
  open: boolean;
  variant: "default" | "error";
  title: string;
  message: ReactNode;
};

const closedSnackbar = (): SnackbarState => ({
  open: false,
  variant: "default",
  title: "",
  message: null,
});

export function useSnackbar() {
  const [snackbar, setSnackbar] = useState<SnackbarState>(closedSnackbar);

  const showSnackbar = (state: Omit<SnackbarState, "open">) => {
    setSnackbar({ ...state, open: true });
  };

  const dismissSnackbar = () => setSnackbar(closedSnackbar());

  return { snackbar, showSnackbar, dismissSnackbar };
}
