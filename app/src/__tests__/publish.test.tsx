import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { Disclosure } from "../api/types";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { JobPublish } from "../screens/JobPublish";
import { JobPage } from "../screens/JobPage";
import { Settings } from "../screens/Setup";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const de = STRINGS.de;

const DOC: Disclosure = {
  ready: true,
  lang: "de",
  ai: [{ n: 1, kind: "broll", t0: 2, t1: 4.5, model: "zentro-image-1", prompt: "a calm desk", style: "photo", labelled: true }],
  stock: [{ n: 2, source: "pexels", author: "Mira Holm", licence: "Pexels License", attribution: "Video by Mira Holm on Pexels", source_url: null }],
  platforms: [
    { id: "youtube", label: "YouTube: „Veränderte oder synthetische Inhalte“ auf „Ja“", status: "required", why: "realistisch" },
    { id: "tiktok", label: "TikTok: „KI-generierte Inhalte“ einschalten", status: "required", why: "realistisch" },
  ],
  snippet: "Hinweis: Dieses Video enthält KI-generiertes Bildmaterial (zentro-image-1).\nBildnachweise: Video by Mira Holm on Pexels.",
  comment: "Contains AI-generated material: zentro-image-1",
  c2pa: "signed",
};

describe("publish panel", () => {
  it("shows the checklist, the snippet with a copy button and the AI items", async () => {
    const mock = installMock({ step: 0 });
    mock.state.jobs.get("job-done")!.disclosure = DOC;
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    expect(await screen.findByRole("heading", { name: de["disclosure.title"] }, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText(/Veränderte oder synthetische Inhalte/)).toBeTruthy();
    expect((screen.getByLabelText(de["disclosure.snippet"]) as HTMLTextAreaElement).value).toContain("Bildnachweise");
    expect(screen.getByText(/zentro-image-1/, { selector: "li" })).toBeTruthy();
    expect(screen.getByText(de["disclosure.c2pa.signed"])).toBeTruthy();
    await user.click(screen.getByRole("button", { name: de["disclosure.copy"] }));
    expect(await navigator.clipboard.readText()).toBe(DOC.snippet);
    expect(screen.getByRole("button", { name: de["disclosure.copied"] })).toBeTruthy();
  });

  it("says in one line when there is nothing to disclose, and hides before a render", async () => {
    const mock = installMock({ step: 0 });
    mock.state.jobs.get("job-done")!.disclosure = { ready: true, ai: [], stock: [], platforms: [], snippet: "", c2pa: "skipped" };
    const { unmount } = wrap(<JobPublish id="job-done" reel="out/reel.mp4" />);
    expect(await screen.findByText(de["disclosure.nothing"], {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.queryByText(de["disclosure.platforms"])).toBeNull();
    unmount();
    delete mock.state.jobs.get("job-done")!.disclosure;
    const { container } = wrap(<JobPublish id="job-done" reel="out/reel.mp4" />);
    await waitFor(() => expect(mock.state.calls.filter((c) => c.path === "/api/jobs/job-done/disclosure")).toHaveLength(2));
    expect(container.textContent).toBe("");
  });
});

describe("AI label setting", () => {
  it("switches the illustration label and explains why photos are always labelled", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<Settings />);
    const box = await screen.findByRole("checkbox", { name: de["settings.labels.illustrations"] }, { timeout: 3000 });
    expect(box).toBeChecked();
    expect(screen.getByText(de["settings.labels.why"])).toBeTruthy();
    await user.click(box);
    await waitFor(() => expect(mock.state.labels.ai_label_illustrations).toBe(false));
    expect(box).not.toBeChecked();
  });
});
