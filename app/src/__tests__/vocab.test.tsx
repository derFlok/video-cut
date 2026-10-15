import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { Brand, VocabEntry } from "../api/types";
import { addVariant, cleanVocabulary, vocabKey } from "../components/Vocabulary";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { BrandEditor } from "../screens/Brands";
import { JobPage } from "../screens/JobPage";
import { NewJob } from "../screens/NewJob";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>, lang: "de" | "en" = "de") =>
  STRINGS[lang][key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const lastCall = (mock: ReturnType<typeof installMock>, method: string, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.method === method && c.path === path);

describe("vocabulary helpers", () => {
  it("match keys like the server: case, punctuation and spacing do not count", () => {
    expect(vocabKey("Zentro-Via!")).toBe(vocabKey("zentrovia"));
    expect(vocabKey("Äpfel")).toBe("äpfel");
  });

  it("drop empty rows and add a misspelling once", () => {
    const list: VocabEntry[] = [
      { term: " Zentrovia ", variants: [" Zentrowia", ""] },
      { term: "  ", variants: ["x"] },
    ];
    expect(cleanVocabulary(list)).toEqual([{ term: "Zentrovia", variants: ["Zentrowia"] }]);
    const one = addVariant(cleanVocabulary(list), "zentrovia", "Sentrovia");
    expect(one).toEqual([{ term: "Zentrovia", variants: ["Zentrowia", "Sentrovia"] }]);
    expect(addVariant(one, "Zentrovia", "sentrovia")).toBe(one);
    expect(addVariant(one, "Lumo", "Lumoh")).toHaveLength(2);
  });
});

describe("brand Wörterbuch", () => {
  it("shows terms with misspellings as chips, edits them and saves them with the brand", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<BrandEditor id="dark" />);
    const section = (await screen.findByText(t("vocab.brand.title"))).closest("fieldset")!;
    expect(within(section).getByText(t("vocab.explain"))).toBeInTheDocument();
    expect(within(section).getByLabelText(t("vocab.term"))).toHaveValue("Zentrovia");
    expect(within(section).getByText("Zentrowia")).toBeInTheDocument();

    // a second misspelling (Enter makes a chip), remove the first one
    await user.type(within(section).getByLabelText(t("vocab.variants")), "Sentrovia{Enter}");
    await user.click(within(section).getByRole("button", { name: t("vocab.variant.remove", { v: "Zentrowia" }) }));
    // a new term; an empty row is dropped on save
    await user.click(within(section).getByRole("button", { name: `+ ${t("vocab.add")}` }));
    await user.type(within(section).getAllByLabelText(t("vocab.term"))[1], "Lumo Cloud");
    await user.type(within(section).getAllByLabelText(t("vocab.variants"))[1], "Lumocloud,");
    await user.click(within(section).getByRole("button", { name: `+ ${t("vocab.add")}` }));
    await user.click(screen.getByRole("button", { name: t("common.save") }));

    await screen.findByText(t("brands.saved"));
    const body = lastCall(mock, "PUT", "/api/brands/dark")!.body as Brand;
    expect(body.vocabulary).toEqual([
      { term: "Zentrovia", variants: ["Sentrovia"] },
      { term: "Lumo Cloud", variants: ["Lumocloud"] },
    ]);
  });

  it("shows the server's validation of the vocabulary", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<BrandEditor id="dark" />);
    const section = (await screen.findByText(t("vocab.brand.title"))).closest("fieldset")!;
    await user.type(within(section).getByLabelText(t("vocab.variants")), "Zentro.*{Enter}");
    await user.click(screen.getByRole("button", { name: t("common.save") }));
    expect(await within(section).findByText(/must be plain text/)).toBeInTheDocument();
  });

  it("speaks English after the switch", async () => {
    installMock({ step: 0 });
    wrap(<BrandEditor id="dark" />, "en");
    expect(await screen.findByText(t("vocab.brand.title", {}, "en"))).toBeInTheDocument();
    expect(screen.getByLabelText(t("vocab.term", {}, "en"))).toHaveValue("Zentrovia");
  });
});

describe("terms for one video", () => {
  it("sends the terms of a new video with the upload", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 2000));
    await user.click(screen.getByText(t("vocab.job.title")));
    await user.click(screen.getByRole("button", { name: `+ ${t("vocab.add")}` }));
    await user.type(screen.getByLabelText(t("vocab.term")), "Kalda");
    await user.type(screen.getByLabelText(t("vocab.variants")), "Calda{Enter}Kalla{Enter}");
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/jobs\/job-/));
    const form = lastCall(mock, "POST", "/api/jobs")!.body as FormData;
    expect(JSON.parse(String(form.get("vocabulary")))).toEqual([{ term: "Kalda", variants: ["Calda", "Kalla"] }]);
  });

  it("offers a suggestion, adds it to the brand's Wörterbuch, applies it and sets the captions again", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    await user.click(await screen.findByText(t("vocab.job.title")));
    const panel = screen.getByTestId("job-vocab");
    expect(await within(panel).findByText(t("vocab.job.brand", { n: 1 }))).toBeInTheDocument();
    const line = await within(panel).findByText(t("vocab.suggest.line", { heard: "Sentrovia", term: "Zentrovia", n: 2 }));
    await user.click(within(line.closest("li")!).getByRole("button", { name: t("vocab.suggest.add") }));

    expect(await within(panel).findByText(t("vocab.applied.captions", { n: 2, c: 2 }))).toBeInTheDocument();
    // the term lives in the brand, so the misspelling went there (not into the job)
    const brandBody = lastCall(mock, "PUT", "/api/brands/dark")!.body as Brand;
    expect(brandBody.vocabulary).toEqual([{ term: "Zentrovia", variants: ["Zentrowia", "Sentrovia"] }]);
    expect(lastCall(mock, "PUT", "/api/jobs/job-done/vocabulary")).toBeUndefined();
    expect(within(panel).queryByTestId("vocab-suggestions")).toBeNull();

    const banner = await screen.findByTestId("vocab-overlays");
    expect(within(banner).getByText(t("vocab.overlays.stale"))).toBeInTheDocument();
    await user.click(within(banner).getByRole("button", { name: t("vocab.overlays.redo") }));
    expect(lastCall(mock, "POST", "/api/jobs/job-done/overlays")).toBeDefined();
    await waitFor(() => expect(screen.queryByTestId("vocab-overlays")).toBeNull());
  });

  it("saves the job's own terms and applies them to the transcript", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    await user.click(await screen.findByText(t("vocab.job.title")));
    const panel = screen.getByTestId("job-vocab");
    await user.click(await within(panel).findByRole("button", { name: `+ ${t("vocab.add")}` }));
    await user.type(within(panel).getByLabelText(t("vocab.term")), "Zentrovia");
    await user.type(within(panel).getByLabelText(t("vocab.variants")), "Sentrovia{Enter}");
    await user.click(within(panel).getByRole("button", { name: t("vocab.save.apply") }));
    await within(panel).findByText(t("vocab.applied.captions", { n: 2, c: 2 }));
    expect(lastCall(mock, "PUT", "/api/jobs/job-done/vocabulary")!.body).toEqual({
      vocabulary: [{ term: "Zentrovia", variants: ["Sentrovia"] }],
    });
    expect(lastCall(mock, "POST", "/api/jobs/job-done/vocabulary/apply")).toBeDefined();
  });
});
