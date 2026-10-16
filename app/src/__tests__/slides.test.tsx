import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { JobPage } from "../screens/JobPage";
import { NewJob } from "../screens/NewJob";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], lang: "de" | "en" = "de") => STRINGS[lang][key];
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const lastCall = (mock: ReturnType<typeof installMock>, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.path === path);

describe("slides in the new-job form", () => {
  it("offers none / own / Claude / both next to the screenshots, German by default", async () => {
    installMock({ step: 0 });
    wrap(<NewJob />);
    const group = await screen.findByTestId("new-slides");
    expect(within(group).getByText(t("slides.title"))).toBeInTheDocument();
    expect(within(group).getAllByRole("radio").map((r) => r.parentElement!.textContent)).toEqual([
      "Keine",
      "Eigene hochladen (PDF oder Bilder)",
      "Claude erstellt sie",
      "Beides",
    ]);
    expect(within(group).getByRole("radio", { name: "Keine" })).toBeChecked();
    expect(within(group).queryByLabelText(t("slides.wish"))).not.toBeInTheDocument();
  });

  it("sends mode, wish and the files; upload needs at least one file", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    const group = screen.getByTestId("new-slides");
    await user.click(within(group).getByRole("radio", { name: t("slides.mode.upload") }));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    expect(await screen.findByText(t("slides.err.none"))).toBeInTheDocument();
    expect(lastCall(mock, "/api/jobs")).toBeUndefined();

    await user.click(within(group).getByRole("radio", { name: t("slides.mode.both") }));
    const wish = within(group).getByLabelText(t("slides.wish"));
    expect(wish).toHaveAttribute("placeholder", t("slides.wish.placeholder"));
    await user.type(wish, "Die drei Schritte als Ablauf");
    await user.upload(within(group).getByLabelText(new RegExp(t("slides.files"))), [file("deck.pdf", 300), file("p.png", 50)]);
    expect(within(group).getByText("deck.pdf")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs")).toBeTruthy());
    const form = lastCall(mock, "/api/jobs")!.body as FormData;
    expect(form.get("slides_mode")).toBe("both");
    expect(form.get("slides_wish")).toBe("Die drei Schritte als Ablauf");
    expect((form.getAll("slides") as File[]).map((f) => f.name)).toEqual(["deck.pdf", "p.png"]);
  });

  it("refuses a file that is not a PDF or an image, in English too", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup({ applyAccept: false });
    wrap(<NewJob />, "en");
    const group = await screen.findByTestId("new-slides");
    await user.click(within(group).getByRole("radio", { name: t("slides.mode.upload", "en") }));
    await user.upload(within(group).getByLabelText(new RegExp(t("slides.files", "en"))), file("deck.key", 30));
    await user.click(screen.getByRole("button", { name: t("new.submit", "en") }));
    expect(await screen.findByText(/deck\.key: please a PDF or an image/)).toBeInTheDocument();
  });

  it("sends nothing about slides when none is picked", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs")).toBeTruthy());
    const form = lastCall(mock, "/api/jobs")!.body as FormData;
    expect(form.get("slides_mode")).toBeNull();
    expect(form.getAll("slides")).toEqual([]);
  });
});

describe("slides at the plan gate", () => {
  it("shows each slide as a card with still, template, moment and layout in plain words", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-approval" />);
    const cards = await screen.findByTestId("plan-slides");
    const s1 = cards.querySelector('[data-slide="s1"]') as HTMLElement;
    expect(within(s1).getByText("Schritte")).toBeInTheDocument();
    expect(within(s1).getByText(t("slides.layout.slide"))).toBeInTheDocument();
    expect(within(s1).getByText(/Die Antwort mit dem Beispiel aus dem Alltag/)).toBeInTheDocument();
    expect(within(s1).getByRole("img", { name: "Schritte" })).toBeInTheDocument();
    const s2 = cards.querySelector('[data-slide="s2"]') as HTMLElement;
    expect(within(s2).getByText(t("slides.tpl.image"))).toBeInTheDocument();
    expect(within(s2).getByText(t("slides.layout.slide_full"))).toBeInTheDocument();
  });

  it("removes a slide or sends feedback on it as plan feedback with the slide id", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    const cards = await screen.findByTestId("plan-slides");
    const s2 = cards.querySelector('[data-slide="s2"]') as HTMLElement;
    await user.click(within(s2).getByRole("button", { name: t("slides.plan.remove") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")!.body).toEqual({ text: "Folie s2 entfernen.", slide: "s2" });
  });

  it("sends a typed change for one slide", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    const cards = await screen.findByTestId("plan-slides");
    const s1 = cards.querySelector('[data-slide="s1"]') as HTMLElement;
    await user.type(within(s1).getByLabelText(t("slides.plan.feedback")), "Erst beim zweiten Satz");
    await user.click(within(s1).getByRole("button", { name: t("slides.plan.send") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")!.body).toEqual({ text: "Erst beim zweiten Satz", slide: "s1" });
  });
});

describe("slides in the result", () => {
  it("marks slide moments on a strip under the player and seeks there", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-done" />);
    const video = (await screen.findByText(t("result.title"))).closest("section")!.querySelector("video")!;
    Object.defineProperty(video, "duration", { value: 40, configurable: true });
    fireEvent.loadedMetadata(video);
    const strip = await screen.findByTestId("slide-marks");
    const mark = within(strip).getByRole("button", { name: "Folie s1 bei 0:08" });
    expect(mark.style.left).toBe("20.5%");
    fireEvent.click(mark);
    expect(video.currentTime).toBeCloseTo(8.2);
  });
});

describe("verify flag of a seam caption", () => {
  it("names overlay_on_slide_content in plain words (de/en)", async () => {
    const { verifyLines } = await import("../screens/Result");
    const { STRINGS: S } = await import("../i18n");
    const v = { ok: false, overlay_flagged: [{ scene: "caption", t0: 3, flags: ["overlay_on_slide_content"] }] };
    for (const lang of ["de", "en"] as const) {
      const t = (k: string, vars?: Record<string, unknown>) =>
        (S[lang] as Record<string, string>)[k].replace(/\{(\w+)\}/g, (_, x: string) => String(vars?.[x] ?? ""));
      expect(verifyLines(v as never, t as never)[0]).toContain(S[lang]["flag.overlay_on_slide_content"]);
    }
  });
});
