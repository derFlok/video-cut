import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { JobPage } from "../screens/JobPage";
import { NewJob } from "../screens/NewJob";
import { Settings } from "../screens/Setup";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], lang: "de" | "en" = "de") => STRINGS[lang][key];
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const calls = (mock: ReturnType<typeof installMock>, method: string, path: string) =>
  mock.state.calls.filter((c) => c.method === method && c.path === path);

describe("material in the new-job form", () => {
  it("offers none / stock / AI images / both, starts with the setting's default and sends mode and wish", async () => {
    const mock = installMock({ step: 0, materialSettings: { material_default_mode: "stock" } });
    const user = userEvent.setup();
    wrap(<NewJob />);
    const group = await screen.findByTestId("new-material");
    expect(within(group).getAllByRole("radio").map((r) => r.parentElement!.textContent)).toEqual([
      "Keins",
      "Stock-Material",
      "KI-Bilder",
      "Beides",
    ]);
    await waitFor(() => expect(within(group).getByRole("radio", { name: t("material.mode.stock") })).toBeChecked());
    await user.click(within(group).getByRole("radio", { name: t("material.mode.both") }));
    await user.type(within(group).getByLabelText(t("material.wish")), "ruhige Büroszenen");
    await user.click(screen.getByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/jobs").length).toBe(1));
    const form = calls(mock, "POST", "/api/jobs")[0].body as FormData;
    expect(form.get("material_mode")).toBe("both");
    expect(form.get("material_wish")).toBe("ruhige Büroszenen");
  });

  it("sends nothing about material when none is picked", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/jobs").length).toBe(1));
    const form = calls(mock, "POST", "/api/jobs")[0].body as FormData;
    expect(form.get("material_mode")).toBeNull();
    expect(form.get("material_wish")).toBeNull();
  });
});

describe("material settings", () => {
  it("stores a key write-only: the value goes in the PUT, never back onto the page", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<Settings />);
    const panel = await screen.findByTestId("material-settings");
    const input = within(panel).getByLabelText(t("material.key.label").replace("{name}", "Pexels"));
    expect(input).toHaveAttribute("type", "password");
    // Pixabay is listed first: its key field and its stock checkbox (on by default)
    expect(within(panel).getByLabelText(t("material.key.label").replace("{name}", "Pixabay"))).toHaveAttribute("type", "password");
    expect(within(panel).getByRole("checkbox", { name: "Pixabay" })).toBeChecked();
    // Openverse needs no key: only its checkbox (on by default, last) and a hint
    expect(within(panel).getByRole("checkbox", { name: "Openverse" })).toBeChecked();
    expect(within(panel).queryByLabelText(t("material.key.label").replace("{name}", "Openverse"))).toBeNull();
    expect(panel).toHaveTextContent(t("material.stock.openverse.hint"));
    await user.type(input, "pexelsKey123456789");
    await user.click(within(panel).getByRole("button", { name: t("material.key.save").replace("{name}", "Pexels") }));
    await waitFor(() => expect(calls(mock, "PUT", "/api/settings/secrets").length).toBe(1));
    expect(calls(mock, "PUT", "/api/settings/secrets")[0].body).toEqual({ account: "pexels-api-key", value: "pexelsKey123456789" });
    await waitFor(() => expect(within(panel).getByTestId("secret-pexels-api-key")).toHaveTextContent(t("material.key.stored")));
    expect(input).toHaveValue("");
    expect(document.body.textContent).not.toContain("pexelsKey123456789");
    // remove sends an empty value
    await user.click(within(panel).getByRole("button", { name: t("material.key.remove.label").replace("{name}", "Pexels") }));
    await waitFor(() => expect(calls(mock, "PUT", "/api/settings/secrets").length).toBe(2));
    expect(calls(mock, "PUT", "/api/settings/secrets")[1].body).toEqual({ account: "pexels-api-key", value: "" });
  });

  it("saves the default mode and the generator, and says why a generator is not ready", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<Settings />);
    const panel = await screen.findByTestId("material-settings");
    await user.selectOptions(within(panel).getByLabelText(t("material.settings.default")), "both");
    await waitFor(() => expect(calls(mock, "PUT", "/api/settings/material").at(-1)?.body).toEqual({ material_default_mode: "both" }));
    const provider = within(panel).getByLabelText(t("material.settings.provider")) as HTMLSelectElement;
    const gemini = within(provider).getByRole("option", { name: /Gemini/ }) as HTMLOptionElement;
    expect(gemini.textContent).toContain("no key stored");
  });

  it("always offers the local generator; picking it while not installed highlights the card", async () => {
    const mock = installMock({
      step: 0,
      materialSettings: {
        image_providers: [
          { name: "gemini", available: true, reason: "", registered: true },
          { name: "openai", available: true, reason: "", registered: true },
          { name: "local", available: false, reason: "not installed", registered: false },
        ],
      },
    });
    const user = userEvent.setup();
    wrap(<Settings />);
    const panel = await screen.findByTestId("material-settings");
    const provider = within(panel).getByLabelText(t("material.settings.provider"));
    const local = (await within(provider).findByRole("option", { name: t("material.provider.local.missing") })) as HTMLOptionElement;
    expect(local.disabled).toBe(false);
    await user.selectOptions(provider, "local");
    await waitFor(() => expect(calls(mock, "PUT", "/api/settings/material").at(-1)?.body).toEqual({ image_provider: "local" }));
    expect(screen.getByTestId("local-image-model")).toHaveClass("panel-highlight");
  });
});

describe("the local image model card", () => {
  const card = async () => screen.findByTestId("local-image-model");

  it("says what it is and costs, shows the numbers, downloads, shows progress and cancels", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<Settings />);
    const c = await card();
    expect(c).toHaveTextContent(t("material.local.title"));
    expect(c).toHaveTextContent("40–80 Sekunden");
    expect(c).toHaveTextContent("Einmal 16 GB herunterladen, danach bleiben 8 GB");
    expect(within(c).getByTestId("local-model-facts")).toHaveTextContent("120,4 GB");
    expect(within(c).getByTestId("local-model-state")).toHaveTextContent(t("material.local.state.not_installed"));
    await user.click(within(c).getByRole("button", { name: "Herunterladen (16 GB)" }));
    await waitFor(() => expect(calls(mock, "POST", "/api/models/image/install").length).toBe(1));
    const prog = await within(c).findByTestId("local-model-progress");
    expect(prog).toHaveTextContent(t("material.local.step.download"));
    expect(prog).toHaveTextContent("4 von 16 GB · 400 Mbit/s, noch etwa 4 Min.");
    expect(within(prog).getByRole("progressbar")).toHaveAttribute("value", "25");
    await user.click(within(prog).getByRole("button", { name: t("material.local.cancel") }));
    await waitFor(() => expect(within(c).getByTestId("local-model-state")).toHaveTextContent(t("material.local.state.partial")));
    expect(within(c).getByRole("button", { name: t("material.local.resume") })).toBeEnabled();
  });

  it("polls every 2 s while it installs", async () => {
    const mock = installMock({ step: 0, imageModel: { state: "installing", progress: { step: "preparing", pct: 100, downloaded_gb: 16 } } });
    wrap(<Settings />);
    const c = await card();
    expect(await within(c).findByTestId("local-model-progress")).toHaveTextContent(t("material.local.step.preparing"));
    const before = calls(mock, "GET", "/api/models/image").length;
    mock.state.imageModel = { ...mock.state.imageModel, state: "installed", installed_gb: 8.1, mflux_version: "0.23.0", progress: null };
    await waitFor(() => expect(within(c).getByTestId("local-model-installed")).toHaveTextContent("Installiert, 8,1 GB"), { timeout: 4000 });
    expect(calls(mock, "GET", "/api/models/image").length).toBeGreaterThan(before);
  });

  it("is disabled with the reason on a Mac with too little memory or disk", async () => {
    installMock({ step: 0, imageModel: { state: "unsupported", memory_gb: 8 } });
    const { unmount } = wrap(<Settings />, "en");
    let c = await card();
    expect(within(c).getByTestId("local-model-blocked")).toHaveTextContent("This Mac has 8 GB memory; the image model needs at least 16 GB.");
    expect(within(c).getByRole("button", { name: "Download (16 GB)" })).toBeDisabled();
    unmount();
    installMock({ step: 0, imageModel: { disk_ok: false, free_gb: 10.2, need_gb: 26 } });
    wrap(<Settings />);
    c = await card();
    expect(within(c).getByTestId("local-model-blocked")).toHaveTextContent("26 GB frei sein, frei sind 10 GB");
    expect(within(c).getByRole("button", { name: "Herunterladen (16 GB)" })).toBeDisabled();
  });

  it("removes after a confirmation", async () => {
    const mock = installMock({ step: 0, imageModel: { state: "installed", installed_gb: 8, mflux_version: "0.23.0" } });
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    wrap(<Settings />);
    const c = await card();
    await user.click(await within(c).findByRole("button", { name: t("material.local.remove") }));
    expect(confirm.mock.calls[0][0]).toContain("8 GB frei");
    expect(calls(mock, "DELETE", "/api/models/image").length).toBe(0);
    await user.click(within(c).getByRole("button", { name: t("material.local.remove") }));
    await waitFor(() => expect(within(c).getByTestId("local-model-state")).toHaveTextContent(t("material.local.state.not_installed")));
    expect(calls(mock, "DELETE", "/api/models/image").length).toBe(1);
    confirm.mockRestore();
  });

  it("shows a failure with the log lines and retries", async () => {
    const mock = installMock({
      step: 0,
      imageModel: { state: "failed", error: "Not installed: network down", log_tail: ["▸ Downloading model flux2-klein-4b", "✗ Not installed: network down"] },
    });
    const user = userEvent.setup();
    wrap(<Settings />);
    const c = await card();
    expect(within(c).getByRole("alert")).toHaveTextContent("network down");
    expect(within(c).getByTestId("local-model-log")).toHaveTextContent("Downloading model");
    await user.click(within(c).getByRole("button", { name: t("material.local.retry") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/models/image/install").length).toBe(1));
  });
});

describe("the material screen", () => {
  it("shows the wishes with candidates, licence, the AI marker; choose / none / search again", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-material" />);
    const scr = await screen.findByTestId("material-screen");
    const w1 = await within(scr).findByTestId("wish-1");
    const w2 = within(scr).getByTestId("wish-2");
    expect(within(w1).getAllByText(/Pexels License/).length).toBeGreaterThan(0);
    // an Openverse candidate: licence with its link, the credit, the source link
    const ov = within(w1).getByTestId("cand-c3");
    expect(within(ov).getByRole("link", { name: "CC BY 2.0" })).toHaveAttribute("href", "https://creativecommons.org/licenses/by/2.0/");
    expect(ov).toHaveTextContent("Lake by Beispiel 3, CC BY 2.0, via Openverse/wikimedia");
    expect(within(ov).getByRole("link", { name: t("material.source") })).toHaveAttribute("href", "https://commons.wikimedia.org/");
    // under 1280 px: marked as upscaled; the large ones are not
    expect(within(ov).getByTestId("cand-small")).toHaveTextContent(t("material.small"));
    expect(within(within(w1).getByTestId("cand-c1")).queryByTestId("cand-small")).toBeNull();
    expect(within(w1).queryByText(t("material.ai"))).toBeNull(); // broll: stock only
    expect(within(w2).getAllByText(t("material.ai")).length).toBe(2); // two generated stills
    expect(within(scr).getByTestId("material-open")).toHaveTextContent("2");

    await user.click(within(within(w1).getByTestId("cand-c2")).getByRole("button", { name: t("material.choose") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/jobs/job-material/material/choose").at(-1)?.body).toEqual({ n: 1, candidate: "c2" }));
    await waitFor(() => expect(within(w1).getByText(t("material.chosen").replace("{id}", "c2"))).toBeInTheDocument());

    await user.click(within(w2).getByRole("button", { name: t("material.none") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/jobs/job-material/material/choose").at(-1)?.body).toEqual({ n: 2, candidate: null }));
    expect(await within(scr).findByRole("button", { name: t("material.continue") })).toBeEnabled();

    // search again with an edited query (one wish only)
    await user.click(within(w1).getByText(t("material.redo")));
    const q = within(w1).getByLabelText(t("material.query"));
    await user.clear(q);
    await user.type(q, "city street evening");
    await user.click(within(w1).getByRole("button", { name: t("material.search") }));
    await waitFor(() =>
      expect(calls(mock, "POST", "/api/jobs/job-material/material/fetch").at(-1)?.body).toEqual({ wish: 1, query: "city street evening" }),
    );
  });

  it("with the local generator but no model, says where to get it instead of a generate button", async () => {
    const mock = installMock({ step: 0 });
    mock.state.jobs.get("job-material")!.material!.image_provider = "local";
    const user = userEvent.setup();
    wrap(<JobPage id="job-material" />);
    const w2 = await screen.findByTestId("wish-2");
    await user.click(within(w2).getByText(t("material.redo")));
    const hint = await within(w2).findByTestId("generate-missing-2");
    expect(hint).toHaveTextContent(t("material.generate.local.missing"));
    expect(within(hint).getByRole("link", { name: t("material.generate.local.link") })).toHaveAttribute("href", "#/settings");
    expect(within(w2).queryByRole("button", { name: t("material.generate") })).toBeNull();
  });

  it("regenerates with an edited prompt and continues to the cut when everything is decided", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-material" />);
    const w2 = await screen.findByTestId("wish-2");
    await user.click(within(w2).getByText(t("material.redo")));
    const p = within(w2).getByLabelText(t("material.prompt"));
    await user.clear(p);
    await user.type(p, "A calm desk with plants");
    await user.click(within(w2).getByRole("button", { name: t("material.generate") }));
    await waitFor(() =>
      expect(calls(mock, "POST", "/api/jobs/job-material/material/fetch").at(-1)?.body).toEqual({
        wish: 2,
        prompt: "A calm desk with plants",
        generate: true,
      }),
    );
    // after the run: decide both and go on
    const scr = await screen.findByTestId("material-screen");
    await waitFor(() => expect(within(scr).getByTestId("wish-1")).toBeInTheDocument());
    for (const n of [1, 2]) {
      const w = await within(scr).findByTestId(`wish-${n}`);
      await waitFor(() => expect(within(w).getByRole("button", { name: t("material.none") })).toBeEnabled());
      await user.click(within(w).getByRole("button", { name: t("material.none") }));
    }
    await user.click(await within(scr).findByRole("button", { name: t("material.continue") }));
    await waitFor(() => expect(calls(mock, "POST", "/api/jobs/job-material/start").length).toBe(1));
  });

  it("lists the plan's wishes in the plan view", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-material" />);
    await screen.findByTestId("material-screen");
    await user.click(screen.getByText(t("job.plan.show")));
    const list = await screen.findByTestId("plan-material");
    expect(within(list).getByText(t("material.kind.broll"))).toBeInTheDocument();
    expect(within(list).getByText(/team meeting office calm/)).toBeInTheDocument();
  });
});
