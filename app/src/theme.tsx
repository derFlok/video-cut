// The app's look follows the default brand: the server turns the brand into a stylesheet of
// CSS tokens (videocut/theme.py) and serves it inline in index.html as <style id="brand-theme">
// plus the same view as JSON, so the first paint is already themed. This module keeps that
// element current after a click (no reload) and lets the brand editor preview a brand.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type { BrandSettings, ThemeView } from "./api/types";

export const STYLE_ID = "brand-theme";
export const NEUTRAL: ThemeView = { brand_id: null, name: null, css: "", logo: { light: null, dark: null }, notes: [] };

/** The view the server inlined into the page, if any. */
export function servedTheme(doc: Document = document): ThemeView | null {
  const el = doc.getElementById(`${STYLE_ID}-data`);
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as ThemeView;
  } catch {
    return null;
  }
}

/** Put `css` into the page's theme element (created when missing; "" is neutral). */
export function applyThemeCss(css: string, doc: Document = document): void {
  let el = doc.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!el) {
    el = doc.createElement("style");
    el.id = STYLE_ID;
    doc.head.appendChild(el);
  }
  if (el.textContent !== css) el.textContent = css;
}

interface ThemeCtx {
  /** The real theme (default brand), not the preview. */
  theme: ThemeView;
  /** What the page shows: the preview while one is on, else the real theme. */
  shown: ThemeView;
  settings: BrandSettings | null;
  saveSettings: (changes: Partial<BrandSettings>) => Promise<void>;
  reload: () => Promise<void>;
  setPreview: (v: ThemeView | null) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ThemeView>(() => servedTheme() ?? NEUTRAL);
  const [preview, setPreview] = useState<ThemeView | null>(null);

  const reload = useCallback(async () => {
    try {
      setTheme(await api.theme());
    } catch {
      /* an older server without themes: stay as served */
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);

  const saveSettings = useCallback(async (changes: Partial<BrandSettings>) => {
    setTheme(await api.saveBrandSettings(changes));
  }, []);

  const shown = preview ?? theme;
  useEffect(() => applyThemeCss(shown.css), [shown.css]);

  const value = useMemo(
    () => ({ theme, shown, settings: theme.settings ?? null, saveSettings, reload, setPreview }),
    [theme, shown, saveSettings, reload],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

const FALLBACK: ThemeCtx = {
  theme: NEUTRAL,
  shown: NEUTRAL,
  settings: null,
  saveSettings: async () => undefined,
  reload: async () => undefined,
  setPreview: () => undefined,
};

/** Screens rendered without the provider (tests) see a neutral theme that does nothing. */
export function useTheme(): ThemeCtx {
  return useContext(Ctx) ?? FALLBACK;
}
