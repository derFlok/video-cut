import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { NewJob } from "../screens/NewJob";
import { STYLE_ID, ThemeProvider, applyThemeCss, servedTheme } from "../theme";

const wrap = (ui: ReactNode) =>
  render(
    <I18nProvider lang="de">
      <ThemeProvider>{ui}</ThemeProvider>
    </I18nProvider>,
  );
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>) =>
  STRINGS.de[key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const themeCss = () => document.getElementById(STYLE_ID)?.textContent ?? "";

describe("default brand and theme", () => {
  it("reads the theme the server inlined and keeps one style element", () => {
    const data = document.createElement("script");
    data.type = "application/json";
    data.id = `${STYLE_ID}-data`;
    data.textContent = JSON.stringify({ brand_id: "x", name: "X", css: "html:root{--bg:#000000}", logo: { light: null, dark: null }, notes: [] });
    document.head.appendChild(data);
    expect(servedTheme()?.brand_id).toBe("x");
    data.remove();
    applyThemeCss("a{}");
    applyThemeCss("b{}");
    expect(document.querySelectorAll(`#${STYLE_ID}`)).toHaveLength(1);
    expect(themeCss()).toBe("b{}");
    applyThemeCss("");
  });

  it("sets a brand as default on the Brands screen and the whole app follows at once", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    window.location.hash = "#/brands";
    wrap(<App />);
    await user.click(await screen.findByRole("button", { name: `${t("brands.default.set")}: Beispiel dunkel` }));
    expect(await screen.findByText(t("brands.default.badge"))).toBeInTheDocument();
    expect(mock.state.brandSettings.default_brand).toBe("dark");
    await waitFor(() => expect(themeCss()).toContain("--bg:#121417"));
    expect(screen.getByText(t("brands.default.is", { name: "Beispiel dunkel" }))).toBeInTheDocument();

    // keep the default brand but a neutral UI
    await user.click(screen.getByLabelText(t("theme.follow")));
    await waitFor(() => expect(themeCss()).toBe(""));
    expect(mock.state.brandSettings).toEqual({ default_brand: "dark", theme_follows_brand: false });
    await user.click(screen.getByLabelText(t("theme.follow")));
    await waitFor(() => expect(themeCss()).toContain("--bg:#121417"));

    // back to neutral
    await user.click(screen.getByRole("button", { name: t("brands.default.unset") }));
    await waitFor(() => expect(themeCss()).toBe(""));
    expect(screen.queryByText(t("brands.default.badge"))).not.toBeInTheDocument();
  });

  it("preselects the default brand in the new-job form, neutral stays selectable", async () => {
    installMock({ step: 0, brandSettings: { default_brand: "light" } });
    const user = userEvent.setup();
    wrap(<NewJob />);
    const select = (await screen.findByLabelText(t("new.brand"))) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("light"));
    await user.selectOptions(select, "");
    expect(select.value).toBe("");
    expect(screen.getByRole("option", { name: t("new.brand.none") })).toBeInTheDocument();
  });

  it("previews the edited brand live, shows the contrast hint, and restores on leaving", async () => {
    installMock({ step: 0, brandSettings: { default_brand: "dark" } });
    const user = userEvent.setup();
    window.location.hash = "#/brands/light";
    wrap(<App />);
    await waitFor(() => expect(themeCss()).toContain("--bg:#121417"));
    const text = await screen.findByLabelText(t("brands.color.text"));
    await user.clear(text);
    await user.type(text, "#EEEEEE"); // pale text on a pale background
    expect(await screen.findByText(t("theme.note.text", { ratio: "1,1" }), {}, { timeout: 2000 })).toBeInTheDocument();
    expect(themeCss()).toContain("--bg:#121417"); // no preview yet: the real theme
    await user.click(screen.getByLabelText(t("theme.preview")));
    await waitFor(() => expect(themeCss()).toContain("--text:#EEEEEE"));
    window.location.hash = "#/brands";
    await waitFor(() => expect(themeCss()).toContain("--bg:#121417"));
  });

  it("deleting the default brand makes the app neutral again", async () => {
    const mock = installMock({ step: 0, brandSettings: { default_brand: "light" } });
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.location.hash = "#/brands/light";
    wrap(<App />);
    await waitFor(() => expect(themeCss()).not.toBe(""));
    await user.click(await screen.findByRole("button", { name: t("brands.delete") }));
    await waitFor(() => expect(themeCss()).toBe(""));
    expect(mock.state.brands.has("light")).toBe(false);
    expect(mock.state.brandSettings.default_brand).toBeNull();
    vi.restoreAllMocks();
  });
});
