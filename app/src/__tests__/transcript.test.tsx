import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Brand, TranscriptView, TranscriptWord } from "../api/types";
import { SEEK_EVENT, sentences, visibleWords } from "../components/Transcript";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { JobPage } from "../screens/JobPage";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>, lang: "de" | "en" = "de") =>
  STRINGS[lang][key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const lastCall = (mock: ReturnType<typeof installMock>, method: string, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.method === method && c.path === path);

const word = (i: number, w: string, out: number | null): TranscriptWord => ({ i, w, t0: i, t1: i + 0.5, out_t0: out, out_t1: out === null ? null : out + 0.5 });

async function openTranscript() {
  const user = userEvent.setup();
  wrap(<JobPage id="job-done" />);
  await user.click(await screen.findByText(t("transcript.title")));
  const panel = screen.getByTestId("job-transcript");
  await within(panel).findByTestId("transcript-text");
  return { user, panel };
}

describe("transcript helpers", () => {
  it("group words into sentences and hide what the cut removed", () => {
    const ws = [word(0, "Hallo.", 0), word(1, "Das", null), word(2, "ist", 1), word(3, "gut!", 2), word(4, "Und", 3)];
    expect(sentences(ws).map((g) => g.map((w) => w.w))).toEqual([["Hallo."], ["Das", "ist", "gut!"], ["Und"]]);
    const view = { words: ws, has_reel: true } as TranscriptView;
    expect(visibleWords(view, false).map((w) => w.i)).toEqual([0, 2, 3, 4]);
    expect(visibleWords(view, true)).toHaveLength(5);
    expect(visibleWords({ ...view, has_reel: false }, false)).toHaveLength(5);
  });
});

describe("Transkript on the job page", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
  });

  it("shows the words in the cut, edits one inline, seeks the player and offers the refresh", async () => {
    const mock = installMock({ step: 0 });
    const seeks: number[] = [];
    const onSeek = (e: Event) => seeks.push((e as CustomEvent<{ t: number }>).detail.t);
    window.addEventListener(SEEK_EVENT, onSeek);
    const { user, panel } = await openTranscript();
    const text = within(panel).getByTestId("transcript-text");
    // "Ähm," is cut: only shown with the toggle
    expect(within(text).queryByRole("button", { name: "Ähm," })).toBeNull();
    await user.click(within(panel).getByLabelText(t("transcript.all")));
    expect(within(text).getAllByRole("button", { name: "Ähm," }).length).toBeGreaterThan(0);

    await user.click(within(text).getAllByRole("button", { name: "Zentrowia" })[0]);
    expect(seeks[0]).toBeCloseTo(2.0);
    const input = within(panel).getByLabelText(t("transcript.edit.label", { w: "Zentrowia" }));
    // Esc cancels
    await user.type(input, "{Escape}");
    expect(within(panel).queryByLabelText(t("transcript.edit.label", { w: "Zentrowia" }))).toBeNull();
    expect(lastCall(mock, "PATCH", "/api/jobs/job-done/transcript")).toBeUndefined();

    await user.click(within(text).getAllByRole("button", { name: "Zentrowia" })[0]);
    const again = within(panel).getByLabelText(t("transcript.edit.label", { w: "Zentrowia" }));
    await user.clear(again);
    await user.type(again, "Zentrovia{Enter}");
    await waitFor(() => expect(lastCall(mock, "PATCH", "/api/jobs/job-done/transcript")?.body).toEqual({ i: 5, text: "Zentrovia", t0: 2 }));
    const edited = await within(text).findByRole("button", { name: "Zentrovia" });
    expect(edited).toHaveClass("ts-edited");
    expect(edited).toHaveAttribute("title", t("transcript.was", { w: "Zentrowia" }));
    expect(within(panel).getByText(new RegExp(t("transcript.count", { n: "\\d+", e: 1 })))).toBeInTheDocument();

    // the refresh banner (no Claude) runs the tool steps
    const banner = await screen.findByTestId("captions-refresh");
    expect(screen.queryByTestId("vocab-overlays")).toBeNull();
    await user.click(within(banner).getByRole("button", { name: t("vocab.overlays.redo") }));
    await waitFor(() => expect(lastCall(mock, "POST", "/api/jobs/job-done/captions/refresh")).toBeDefined());
    await waitFor(() => expect(screen.queryByTestId("captions-refresh")).toBeNull());

    // undo the edit
    await user.click(within(panel).getByRole("button", { name: t("transcript.undo") }));
    await waitFor(() => expect(within(text).queryByRole("button", { name: "Zentrovia" })).toBeNull());
    window.removeEventListener(SEEK_EVENT, onSeek);
  });

  it("searches and remembers a spelling in the brand's Wörterbuch", async () => {
    const mock = installMock({ step: 0 });
    const { user, panel } = await openTranscript();
    await user.type(within(panel).getByLabelText(t("transcript.search")), "bericht");
    expect(within(panel).getByText(t("transcript.hits", { n: 3 }))).toBeInTheDocument();
    const text = within(panel).getByTestId("transcript-text");
    expect(within(text).getAllByRole("button", { name: "Bericht" })[0]).toHaveClass("ts-hit");

    await user.click(within(text).getAllByRole("button", { name: "Bericht" })[0]);
    const input = within(panel).getByLabelText(t("transcript.edit.label", { w: "Bericht" }));
    await user.clear(input);
    await user.type(input, "Monatsbericht");
    await user.click(within(panel).getByLabelText(t("transcript.always")));
    await user.click(within(panel).getByRole("button", { name: t("common.save") }));
    await waitFor(() => expect(lastCall(mock, "POST", "/api/jobs/job-done/vocabulary/apply")).toBeDefined());
    const body = lastCall(mock, "PUT", "/api/brands/dark")?.body as Brand | undefined;
    const job = mock.state.jobs.get("job-done")!.detail;
    if (job.brand === "dark") expect(body?.vocabulary).toContainEqual({ term: "Monatsbericht", variants: ["Bericht"] });
    else expect(lastCall(mock, "PUT", "/api/jobs/job-done/vocabulary")?.body).toEqual({ vocabulary: expect.arrayContaining([{ term: "Monatsbericht", variants: ["Bericht"] }]) });
    expect(await within(panel).findByText(/Monatsbericht/, { selector: "p" })).toBeInTheDocument();
  });

  it("refuses too long text in the field and speaks English", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />, "en");
    await user.click(await screen.findByText(t("transcript.title", {}, "en")));
    const panel = screen.getByTestId("job-transcript");
    const text = await within(panel).findByTestId("transcript-text");
    await user.click(within(text).getAllByRole("button", { name: "Heute" })[0]);
    const input = within(panel).getByLabelText(t("transcript.edit.label", { w: "Heute" }, "en"));
    expect(input).toHaveAttribute("maxLength", "60");
    expect(within(panel).getByText(t("transcript.always", {}, "en"))).toBeInTheDocument();
  });
});
