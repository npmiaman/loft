export type Theme = "system" | "light" | "dark";
const KEY = "pigeon-theme";

export function getTheme(): Theme {
  try { const t = localStorage.getItem(KEY); return t === "light" || t === "dark" ? t : "system"; } catch { return "system"; }
}

/** Saved on this computer; applied by setting data-theme on <html> (the tokens in globals.css read it). */
export function setTheme(t: Theme) {
  try { if (t === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch { /* private mode */ }
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
}
