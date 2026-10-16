import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { Settings } from "../screens/Setup";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>) =>
  STRINGS.de[key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const KEY = "sk-ant-api03-" + "a".repeat(40);

describe("first start", () => {
  it("shows the detected subscription, saves local mode, tests, then the first-video hint", async () => {
    const mock = installMock({ step: 0, setup: { done: false } });
    const user = userEvent.setup();
    wrap(<App />);
    expect(await screen.findByRole("heading", { name: t("setup.title") })).toBeInTheDocument();
    expect(await screen.findByText(t("setup.detected"))).toBeInTheDocument();
    expect(screen.getByText(t("setup.detected.account", { account: "person@example.com" }))).toBeInTheDocument();
    // the job list stays hidden until the connection is chosen
    expect(screen.queryByText(STRINGS.de["home.title"])).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: t("setup.local") })).toBeChecked();
    expect(screen.getByText(t("setup.terms"))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: t("setup.save") }));
    await waitFor(() => expect(mock.state.calls.some((c) => c.path === "/api/setup/auth")).toBe(true));
    expect(mock.state.calls.find((c) => c.path === "/api/setup/auth")!.body).toEqual({ mode: "local" });
    expect(await screen.findByRole("heading", { name: t("first.title") })).toBeInTheDocument();
    expect(screen.getByText(t("first.tip.format"))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: t("setup.test") }));
    expect(await screen.findByText(t("setup.test.ok", { s: 3.4 }))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: t("first.later") }));
    expect(await screen.findByRole("heading", { name: STRINGS.de["home.title"] })).toBeInTheDocument();
  });

  it("without a sign-in: explains both ways, checks the key, stores it, never shows it", async () => {
    const mock = installMock({ step: 0, setup: { done: false, claude: { signed_in: false, account: null, subscription: null } } });
    const user = userEvent.setup();
    wrap(<App />);
    expect(await screen.findByText(t("setup.notSignedIn"))).toBeInTheDocument();
    expect(screen.getByText(t("setup.twoWays"))).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: t("setup.apikey") })).toBeChecked();
    expect(screen.getByRole("link", { name: t("setup.key.get") })).toHaveAttribute("href", "https://console.anthropic.com/settings/keys");
    await user.click(screen.getByRole("button", { name: t("setup.signin.how") }));
    expect(screen.getByText(t("setup.signin.steps"))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: t("setup.save") }));
    expect(await screen.findByText(t("setup.err.nokey"))).toBeInTheDocument();
    await user.type(screen.getByLabelText(t("setup.key.label")), "hello");
    await user.click(screen.getByRole("button", { name: t("setup.save") }));
    expect(await screen.findByText(t("setup.err.key"))).toBeInTheDocument();
    expect(mock.state.calls.some((c) => c.path === "/api/setup/auth")).toBe(false);

    await user.clear(screen.getByLabelText(t("setup.key.label")));
    await user.type(screen.getByLabelText(t("setup.key.label")), KEY);
    await user.click(screen.getByRole("button", { name: t("setup.save") }));
    expect(await screen.findByRole("heading", { name: t("first.title") })).toBeInTheDocument();
    expect(mock.state.apiKey).toBe(KEY);
    expect(screen.getByLabelText(t("setup.key.label"))).toHaveValue("");
    expect(screen.getByLabelText(t("setup.key.label"))).toHaveAttribute("type", "password");
  });

  it("the test button reports a failed connection", async () => {
    installMock({ step: 0, testOk: false });
    const user = userEvent.setup();
    wrap(<Settings />);
    expect(await screen.findByText(t("settings.mode.local"))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t("setup.test") }));
    expect(await screen.findByText(t("setup.test.fail", { msg: "Invalid API key" }))).toBeInTheDocument();
  });

  it("English strings exist for every key", () => {
    for (const k of Object.keys(STRINGS.de) as (keyof (typeof STRINGS)["de"])[]) expect(STRINGS.en[k]).toBeTruthy();
  });
});

describe("settings", () => {
  it("shows the mode and switches to an API key", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<Settings />);
    expect(await screen.findByText(t("settings.mode.local"))).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: t("setup.apikey") }));
    await user.type(screen.getByLabelText(t("setup.key.label")), KEY);
    await user.click(screen.getByRole("button", { name: t("setup.save") }));
    expect(await screen.findByText(t("settings.mode.apikey"))).toBeInTheDocument();
    expect(mock.state.setup.mode).toBe("apikey");
  });
});

describe("quit", () => {
  it("stops the server and shows the closing page", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<App />);
    await user.click(await screen.findByRole("button", { name: t("nav.quit") }));
    expect(await screen.findByRole("heading", { name: t("quit.done.title") })).toBeInTheDocument();
    expect(mock.state.stopped).toBe(true);
  });

  it("asks first while a run is active", async () => {
    const mock = installMock({ step: 0 });
    mock.state.active = "x";
    const user = userEvent.setup();
    const ask = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    wrap(<App />);
    const btn = await screen.findByRole("button", { name: t("nav.quit") });
    await user.click(btn);
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
    expect(mock.state.stopped).toBe(false);
    await user.click(btn);
    expect(await screen.findByRole("heading", { name: t("quit.done.title") })).toBeInTheDocument();
    expect(ask).toHaveBeenCalledWith(t("quit.busy"));
    expect(mock.state.stopped).toBe(true);
    ask.mockRestore();
  });
});
