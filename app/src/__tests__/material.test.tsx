import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
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
    const local = within(provider).getByRole("option", { name: /Lokal/ }) as HTMLOptionElement;
    expect(local.disabled).toBe(true);
    expect(local.textContent).toContain("the image model is not downloaded");
  });

  it("hides the local generator while it is not registered", async () => {
    installMock({
      step: 0,
      materialSettings: {
        image_providers: [
          { name: "gemini", available: true, reason: "", registered: true },
          { name: "openai", available: true, reason: "", registered: true },
          { name: "local", available: false, reason: "not installed", registered: false },
        ],
      },
    });
    wrap(<Settings />);
    const panel = await screen.findByTestId("material-settings");
    const provider = within(panel).getByLabelText(t("material.settings.provider"));
    expect(within(provider).queryByRole("option", { name: /Lokal/ })).toBeNull();
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
