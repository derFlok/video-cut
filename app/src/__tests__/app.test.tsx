import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ApiError, api } from "../api/client";
import type { PdCandidate } from "../api/types";
import { readableTool } from "../components/LiveLog";
import { Markdown } from "../components/Markdown";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { fieldErrors } from "../screens/Brands";
import { JobPage } from "../screens/JobPage";
import { NewJob, validateNewJob } from "../screens/NewJob";
import { PersonalDataDialog, decision } from "../screens/PersonalData";
import { changedKeeps } from "../screens/PlanReview";
import { verifyLines } from "../screens/Result";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>) =>
  STRINGS.de[key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const lastCall = (mock: ReturnType<typeof installMock>, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.path === path);

describe("upload form", () => {
  const types = [
    { id: "a", name_de: "A", name_en: "A", desc_de: "", desc_en: "", needs: ["camera"], optional: [] },
    { id: "b", name_de: "B", name_en: "B", desc_de: "", desc_en: "", needs: ["camera", "screen"], optional: [] },
  ];

  it("validates type, camera, extensions and required inputs", () => {
    expect(validateNewJob({ type: null, camera: null, screen: null, assets: [] }, types, t)).toEqual([
      t("new.err.type"),
      t("new.err.camera"),
    ]);
    const bad = validateNewJob({ type: "a", camera: file("take.txt"), screen: null, assets: [file("x.mov")] }, types, t);
    expect(bad).toHaveLength(2);
    expect(bad[0]).toContain("take.txt");
    expect(bad[1]).toContain("x.mov");
    expect(validateNewJob({ type: "b", camera: file("take.MOV"), screen: null, assets: [] }, types, t)[0]).toContain(
      t("new.screen"),
    );
    expect(validateNewJob({ type: "a", camera: file("take.mp4"), screen: null, assets: [file("s.png")] }, types, t)).toEqual([]);
  });

  it("blocks the upload until the form is valid, then uploads and starts", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await screen.findByText("Gedanke des Tages");
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    expect(await screen.findByText(t("new.err.camera"))).toBeInTheDocument();
    expect(mock.state.calls.some((c) => c.path === "/api/jobs" && c.method === "POST")).toBe(false);

    await user.click(screen.getByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 2000));
    await user.upload(screen.getByLabelText(/^Screenshots/), [file("one.png"), file("two.png")]);
    // reorder: move the second screenshot up
    await user.click(screen.getByRole("button", { name: `${t("new.up")}: two.png` }));
    await user.click(screen.getByText("16:9"));
    await user.type(screen.getByLabelText(t("new.brief")), "Kurz halten");
    await user.click(screen.getByRole("button", { name: t("new.submit") }));

    await waitFor(() => expect(window.location.hash).toMatch(/^#\/jobs\/job-/));
    const form = lastCall(mock, "/api/jobs")!.body as FormData;
    expect(form.get("type")).toBe("thought");
    expect(form.get("format")).toBe("16:9");
    expect(form.get("brief")).toBe("Kurz halten");
    expect((form.get("camera") as File).name).toBe("take.mov");
    expect((form.getAll("assets") as File[]).map((f) => f.name)).toEqual(["two.png", "one.png"]);
    expect(mock.state.calls.some((c) => c.path.endsWith("/start"))).toBe(true);
  });

  it("explains what auto picks", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    expect(await screen.findByText(t("new.format.auto.hint.camera"))).toBeInTheDocument();
    await user.upload(screen.getByLabelText(/^Bildschirmaufnahme/), file("screen.mp4"));
    expect(screen.getByText(t("new.format.auto.hint.screen"))).toBeInTheDocument();
  });
});

describe("personal-data dialog", () => {
  const cands: PdCandidate[] = [
    { id: 1, kind: "email", t0: 1, t1: 2, box: [0, 0, 0.5, 0.5], reason: "r1" },
    { id: 2, kind: "name", asset: "input/a.png", box: [0, 0, 0.5, 0.5], reason: "r2" },
    { id: 3, kind: "phone", t0: 3, t1: 4, box: [0, 0, 0.5, 0.5], reason: "r3" },
  ];

  it("builds the payload per mode", () => {
    expect(decision("redact_all", new Set(), cands)).toEqual({ mode: "redact_all", ids: [1, 2, 3] });
    expect(decision("demo", new Set([1]), cands)).toEqual({ mode: "demo", ids: [] });
    expect(decision("redact", new Set([3, 1]), cands)).toEqual({ mode: "redact", ids: [1, 3] });
  });

  it("defaults to redacting everything and sends the per-row choice", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    wrap(<PersonalDataDialog jobId="j" candidates={cands} onCancel={() => undefined} onConfirm={onConfirm} />);
    expect(screen.getByText(t("pd.default"))).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: t("pd.mode.redact_all") })).toBeChecked();
    const boxes = screen.getAllByRole("checkbox", { name: t("pd.row.redact") });
    boxes.forEach((b) => expect(b).toBeChecked());

    await user.click(screen.getByRole("button", { name: t("pd.confirm") }));
    expect(onConfirm).toHaveBeenLastCalledWith({ mode: "redact_all", ids: [1, 2, 3] });

    await user.click(boxes[1]);
    expect(screen.getByRole("radio", { name: t("pd.mode.redact") })).toBeChecked();
    await user.click(screen.getByRole("button", { name: t("pd.confirm") }));
    expect(onConfirm).toHaveBeenLastCalledWith({ mode: "redact", ids: [1, 3] });

    await user.click(screen.getByRole("radio", { name: t("pd.mode.demo") }));
    boxes.forEach((b) => expect(b).not.toBeChecked());
    await user.click(screen.getByRole("button", { name: t("pd.confirm") }));
    expect(onConfirm).toHaveBeenLastCalledWith({ mode: "demo", ids: [] });
  });

  it("approval sends plan_sha and the decision", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    await user.click(await screen.findByRole("button", { name: t("plan.approve") }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getAllByRole("checkbox")[0]);
    await user.click(within(dialog).getByRole("button", { name: t("pd.confirm") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/approve")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/approve")!.body).toEqual({
      plan_sha: mock.state.jobs.get("job-approval")!.plan!.sha,
      redaction: { mode: "redact", ids: [2] },
    });
    // the run goes on to the result
    expect(await screen.findByRole("button", { name: t("result.feedback.send") }, { timeout: 3000 })).toBeInTheDocument();
  });
});

describe("plan", () => {
  it("renders plan.json with the timeline, views and risks", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-approval" />);
    expect(await screen.findByText(t("plan.keep"))).toBeInTheDocument();
    expect(screen.getAllByText("Die Antwort mit dem Beispiel aus dem Alltag.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("pip").length).toBeGreaterThan(0);
    expect(screen.getByText(/Ton am Ende leicht hallig/)).toBeInTheDocument();
  });

  it("feedback runs Claude and highlights the change", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    const box = await screen.findByLabelText(t("plan.ask"));
    const shaBefore = mock.state.jobs.get("job-approval")!.plan!.sha;
    await user.type(box, "Frage weglassen");
    await user.click(screen.getByRole("button", { name: t("plan.ask.send") }));
    expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")!.body).toEqual({ text: "Frage weglassen" });
    const note = await screen.findByText(/Auf deinen Wunsch: Frage weglassen/);
    expect(note.closest(".banner-change")).toBeTruthy();
    const chip = screen.getByText(t("plan.changed"), { selector: ".chip-change" });
    expect(chip.closest("li")).toHaveTextContent("ohne die Zwischenfrage");
    expect(mock.state.jobs.get("job-approval")!.plan!.sha).not.toBe(shaBefore);
  });

  it("marks only new keep-list entries as changed", () => {
    const a = { keep_list: [{ n: 1, src: [0, 1] as [number, number], text: "x" }] };
    const b = { keep_list: [...a.keep_list, { n: 2, src: [2, 3] as [number, number], text: "y" }] };
    expect([...changedKeeps(a, b)]).toEqual([2]);
    expect(changedKeeps(null, b).size).toBe(0);
  });

  it("shows a stale-plan message on 409 when saving an edit", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    await user.click(await screen.findByRole("button", { name: t("plan.edit") }));
    mock.state.jobs.get("job-approval")!.plan!.sha = "changed-elsewhere";
    await user.type(screen.getByRole("textbox", { name: t("plan.edit") }), "\nmehr");
    await user.click(screen.getByRole("button", { name: t("plan.edit.save") }));
    expect(await screen.findByText(t("error.stale"))).toBeInTheDocument();
  });

  it("renders markdown safely", () => {
    wrap(<Markdown source={"# Titel\n\n- **eins**\n- <b>zwei</b>\n\n| a | b |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("heading", { name: "Titel" })).toBeInTheDocument();
    expect(screen.getByText("eins").tagName).toBe("STRONG");
    expect(screen.getByText("<b>zwei</b>")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

describe("result", () => {
  it("sends at_s from the player when 'at the current position' is on", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    const { container } = wrap(<JobPage id="job-done" />);
    const box = await screen.findByLabelText(t("result.feedback"));
    const video = container.querySelector("video")!;
    Object.defineProperty(video, "currentTime", { configurable: true, value: 12.34 });
    act(() => {
      video.dispatchEvent(new Event("timeupdate"));
    });
    await user.type(box, "Weniger rauszoomen");
    await user.click(screen.getByRole("checkbox", { name: t("result.feedback.at", { t: "0:12" }) }));
    await user.click(screen.getByRole("button", { name: t("result.feedback.send") }));
    expect(lastCall(mock, "/api/jobs/job-done/feedback")!.body).toEqual({ text: "Weniger rauszoomen", at_s: 12.3 });
  });

  it("sends no at_s without the toggle and lists the revision afterwards", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    await user.type(await screen.findByLabelText(t("result.feedback")), "Musik leiser");
    await user.click(screen.getByRole("button", { name: t("result.feedback.send") }));
    expect(lastCall(mock, "/api/jobs/job-done/feedback")!.body).toEqual({ text: "Musik leiser" });
    expect(await screen.findByText("„Musik leiser“", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: t("result.history.play") }).length).toBe(2);
  });

  it("describes verify findings in plain words", () => {
    const lines = verifyLines(
      { ok: false, flagged: [{ cut: 1, t: 21.4, flags: ["blink_at_cut"] }], checks: { loudness: { ok: false } } },
      t,
    );
    expect(lines).toEqual(["Schnitt bei 0:21: blink at cut", "Prüfung „loudness“ nicht bestanden"]);
  });
});

describe("i18n and errors", () => {
  it("switches the language", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<App />);
    expect(await screen.findByRole("heading", { name: "Deine Videos" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sprache wechseln" }));
    expect(await screen.findByRole("heading", { name: "Your videos" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Brands" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
    expect(localStorage.getItem("videocut.lang")).toBe("en");
  });

  it("has every key in both languages", () => {
    expect(Object.keys(STRINGS.en).sort()).toEqual(Object.keys(STRINGS.de).sort());
  });

  it("explains a 409 while another run is active", async () => {
    const mock = installMock({ step: 0 });
    mock.state.active = "job-running";
    const user = userEvent.setup();
    wrap(<JobPage id="job-error" />);
    await user.click(await screen.findByRole("button", { name: t("job.continue") }));
    expect(await screen.findByText(t("error.busy"))).toBeInTheDocument();
  });

  it("shows network failures in plain words", async () => {
    installMock({ step: 0 });
    const spy = vi.spyOn(api, "jobs").mockRejectedValue(new ApiError(0, "network"));
    wrap(<App />);
    expect(await screen.findByText(t("error.network"))).toBeInTheDocument();
    spy.mockRestore();
  });

  it("shows brand validation errors at the field", async () => {
    installMock({ step: 0 });
    window.location.hash = "#/brands/light";
    const user = userEvent.setup();
    wrap(<App />);
    const hex = await screen.findByLabelText(t("brands.color.accent"));
    await user.clear(hex);
    await user.type(hex, "blau");
    await user.click(screen.getByRole("button", { name: t("common.save") }));
    expect(await screen.findByText("'blau' is not a hex colour like #RRGGBB")).toBeInTheDocument();
    expect(fieldErrors(["$.colors.bg: bad", "other"])).toEqual({ "colors.bg": ["bad"], _: ["other"] });
  });

  it("makes tool lines readable", () => {
    expect(readableTool("Bash: videocut tool edl locate 12.4")).toBe("▸ edl locate 12.4");
    expect(readableTool("Read: /x/jobs/a/work/project.md")).toBe("▸ Read work/project.md");
  });
});
