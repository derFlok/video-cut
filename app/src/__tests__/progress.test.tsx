import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { JobDetail, Stage } from "../api/types";
import type { LogLine } from "../components/LiveLog";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { activity, expectedSeconds, isInterim, lockReason, recutEstimate } from "../progress";
import { JobPage } from "../screens/JobPage";
import { NewJob } from "../screens/NewJob";

const wrap = (ui: ReactNode) => render(<I18nProvider lang="de">{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], vars?: Record<string, string | number>) =>
  STRINGS.de[key].replace(/\{(\w+)\}/g, (_, k: string) => String(vars?.[k] ?? ""));
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const lastCall = (mock: ReturnType<typeof installMock>, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.path === path);

const job = (over: Partial<JobDetail> = {}): JobDetail => ({
  id: "j",
  type: "walkthrough",
  brand: null,
  format: "auto",
  format_resolved: "16:9",
  status: "running",
  stage: "prep",
  approved: false,
  plan_sha: null,
  redaction: { candidates: [], decision: null },
  outputs: null,
  verify: null,
  runs: [],
  revisions: [],
  ...over,
});
const log = (text: string): LogLine => ({ kind: "log", text });
const tool = (text: string): LogLine => ({ kind: "tool", text });

describe("cut style", () => {
  it("sends the chosen style with the upload and explains each one", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await screen.findByText("Gedanke des Tages");
    const select = screen.getByLabelText(t("style.new"));
    // automatic says what it stands for (camera only: normal)
    expect(screen.getByRole("option", { name: t("style.auto.is", { style: t("style.standard") }) })).toBeInTheDocument();
    expect(screen.getByTestId("style-desc")).toHaveTextContent(t("style.auto.desc"));
    await user.selectOptions(select, "calm");
    expect(screen.getByTestId("style-desc")).toHaveTextContent(t("style.calm.desc"));
    await user.selectOptions(select, "tight");
    expect(screen.getByTestId("style-desc")).toHaveTextContent(t("style.tight.desc"));

    await user.click(screen.getByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/jobs\/job-/));
    expect((lastCall(mock, "/api/jobs")!.body as FormData).get("cut_style")).toBe("tight");
  });

  it("sends auto when nothing was picked", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/jobs\/job-/));
    expect((lastCall(mock, "/api/jobs")!.body as FormData).get("cut_style")).toBe("auto");
  });

  it("changes the style on the plan without offering a recut", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    const select = await screen.findByRole("combobox", { name: t("style.title") });
    expect(select).toHaveValue("auto");
    expect(screen.getByRole("option", { name: t("style.auto.is", { style: t("style.calm") }) })).toBeInTheDocument();
    await user.selectOptions(select, "tight");
    expect(lastCall(mock, "/api/jobs/job-approval/cut-style")!.body).toEqual({ cut_style: "tight" });
    expect(await screen.findByText(t("style.saved"))).toBeInTheDocument();
    expect(screen.queryByTestId("recut")).not.toBeInTheDocument();
  });

  it("offers a recut with time and cost after the cut, and runs it", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    const select = await screen.findByRole("combobox", { name: t("style.title") });
    expect(screen.queryByTestId("recut")).not.toBeInTheDocument();
    await user.selectOptions(select, "calm");
    const box = await screen.findByTestId("recut");
    // the last cut + overlays runs: 302 s + 210 s, 0.88 + 0.52 USD
    expect(box).toHaveTextContent("ca. 9 Min.");
    expect(box).toHaveTextContent("1,40");
    // a stale reel takes no corrections, and says why
    expect(screen.getByTestId("fb-locked")).toHaveTextContent(t("lock.stale"));
    expect(screen.getByLabelText(t("result.feedback"))).toBeDisabled();

    await user.click(screen.getByRole("button", { name: t("style.recut") }));
    expect(lastCall(mock, "/api/jobs/job-done/recut")).toMatchObject({ method: "POST" });
    // while the reel is remade, the old one is not shown as the result
    expect(await screen.findByTestId("result-pending")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("recut")).not.toBeInTheDocument());
    expect(await screen.findByText(t("now.done"), {}, { timeout: 3000 })).toBeInTheDocument();
    expect(mock.state.jobs.get("job-done")!.detail.cut_stale).toBe(false);
    expect(screen.getByLabelText(t("result.feedback"))).toBeEnabled();
  });

  it("changing back to the style of the cut clears the recut offer", async () => {
    installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    const select = await screen.findByRole("combobox", { name: t("style.title") });
    await user.selectOptions(select, "tight");
    expect(await screen.findByTestId("recut")).toBeInTheDocument();
    await user.selectOptions(select, "standard");
    await waitFor(() => expect(screen.queryByTestId("recut")).not.toBeInTheDocument());
  });
});

describe("what is happening", () => {
  it("names the step per stage in plain words", () => {
    const say = (stage: Stage, lines: LogLine[] = [], kind: Parameters<typeof activity>[2] = null, over: Partial<JobDetail> = {}) =>
      activity(job({ stage, ...over }), true, kind, lines, t);
    expect(say("prep")).toBe(t("now.prep.ingest"));
    expect(say("prep", [log("  ingest --denoise   4.1s"), log("  face   2.0s")])).toBe(t("now.prep.transcribe"));
    expect(say("prep", [log("  ingest --denoise   4.1s")], null, { has_screen: true })).toBe(t("now.prep.sync"));
    expect(say("prep", [log("  ingest --denoise 1s"), log("  sync 1s"), log("  screen 1s")], null, { has_screen: true })).toBe(
      t("now.prep.redact_text"),
    );
    expect(say("plan")).toBe(t("now.plan"));
    expect(say("plan_revise")).toBe(t("now.plan_revise"));
    expect(say("cut")).toBe("Claude schneidet das Video");
    expect(say("cut", [tool("Bash: videocut tool render --job j")])).toBe(t("now.cut.render"));
    expect(say("overlays")).toBe("Claude setzt Untertitel und Einblendungen");
    expect(say("verify", [], "cut")).toBe(t("now.verify"));
    expect(say("verify", [], "revise")).toBe(t("now.verify.revise"));
    expect(say("revise")).toBe(t("now.revise"));
    expect(t("now.prep.transcribe")).toBe("Vorbereitung: Transkript wird erstellt");
  });

  it("describes the idle states", () => {
    const idle = (over: Partial<JobDetail>) => activity(job(over), false, null, [], t);
    expect(idle({ status: "waiting", stage: "approval" })).toBe(t("now.approval"));
    expect(idle({ status: "done", stage: "done" })).toBe(t("now.done"));
    expect(idle({ status: "done", stage: "done", cut_stale: true })).toBe(t("now.stale"));
    expect(idle({ status: "error", stage: "error", failed_stage: "overlays" })).toBe(t("now.error.at", { stage: t("stage.overlays") }));
    expect(idle({ status: "new", stage: "prep" })).toBe(t("now.new"));
  });

  it("estimates the step from the recording length", () => {
    expect(expectedSeconds("cut", null)).toBeNull();
    // a 10-minute recording: the cut took ~24 min, overlays ~18 min on a real run
    expect(Math.round(expectedSeconds("cut", 596)! / 60)).toBe(19);
    expect(Math.round(expectedSeconds("overlays", 596)! / 60)).toBe(16);
    expect(expectedSeconds("approval", 596)).toBeNull();
    expect(recutEstimate(job({ runs: [] })).seconds).toBeNull();
    expect(recutEstimate(job({ runs: [], source_duration_s: 60 })).seconds).toBeGreaterThan(0);
  });

  it("shows the line, the time in this step and the expectation on the job page", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-running" />);
    // the demo job has finished ingest, sync, screen, text and face: transcribing now
    expect(await screen.findByText(t("now.prep.transcribe"))).toBeInTheDocument();
    const meta = await screen.findByTestId("now-meta");
    expect(meta).toHaveTextContent(t("now.since", { t: "0:00" }));
    // 30 s + 0.5 × 251.8 s ≈ 2.6 min
    expect(meta).toHaveTextContent(t("now.expected.video", { about: "ca. 3 Min.", len: "4:11" }));
  });

  it("reports overlays as such on a running job", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-overlays" />);
    expect(await screen.findByText("Claude setzt Untertitel und Einblendungen")).toBeInTheDocument();
  });
});

describe("locked correction box", () => {
  it("gives the reason of the running stage", () => {
    const why = (stage: Stage, kind: Parameters<typeof lockReason>[2] = "cut") =>
      lockReason(job({ stage, approved: true }), true, kind, false, false, t);
    expect(why("overlays")).toBe("Einblendungen laufen noch – Korrekturen sind danach möglich.");
    expect(why("cut")).toBe(t("lock.cut"));
    expect(why("verify")).toBe(t("lock.verify"));
    expect(why("verify", "revise")).toBe(t("lock.verify.revise"));
    expect(why("revise", "revise")).toBe(t("lock.revise"));
    expect(why("plan_revise", "plan")).toBe(t("lock.plan"));
    // only a revision says that a correction is being applied
    for (const s of ["cut", "overlays", "verify", "prep", "plan"] as Stage[])
      expect(why(s)).not.toMatch(/setzt die Korrektur um/);
    expect(lockReason(job({ stage: "done", status: "done" }), false, null, false, false, t)).toBeNull();
    expect(lockReason(job({ stage: "done", status: "done" }), false, null, true, false, t)).toBe(t("lock.sending"));
    expect(lockReason(job({ stage: "error", status: "error" }), false, null, false, true, t)).toBe(t("lock.interim"));
  });

  it("revising on the page locks with the revision's reason", async () => {
    installMock({ step: 5 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    await user.type(await screen.findByLabelText(t("result.feedback")), "Musik leiser");
    await user.click(screen.getByRole("button", { name: t("result.feedback.send") }));
    const locked = await screen.findByTestId("fb-locked");
    await waitFor(() => expect(locked).toHaveTextContent(t("lock.revise")));
    expect(screen.getByLabelText(t("result.feedback"))).toBeDisabled();
    // the old reel stays playable during a revision
    expect(screen.queryByTestId("result-pending")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("fb-locked")).not.toBeInTheDocument(), { timeout: 3000 });
  });
});

describe("intermediate reel", () => {
  it("knows when the reel on disk is only a step on the way", () => {
    expect(isInterim(job({ stage: "overlays", approved: true }), true, "cut")).toBe(true);
    expect(isInterim(job({ stage: "verify", approved: true }), true, "cut")).toBe(true);
    expect(isInterim(job({ stage: "verify", approved: true }), true, "revise")).toBe(false);
    expect(isInterim(job({ stage: "done", status: "done" }), false, null)).toBe(false);
    expect(isInterim(job({ stage: "error", status: "error", failed_stage: "overlays" }), false, null)).toBe(true);
    expect(isInterim(job({ stage: "error", status: "error", failed_stage: "verify" }), false, null)).toBe(false);
  });

  it("hides the cut-only reel while overlays run", async () => {
    installMock({ step: 0 });
    const { container } = wrap(<JobPage id="job-overlays" />);
    expect(await screen.findByTestId("result-pending")).toHaveTextContent(t("result.interim.running"));
    expect(container.querySelector("video")).toBeNull();
    expect(screen.queryByRole("link", { name: t("result.download.mp4") })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(t("result.feedback"))).not.toBeInTheDocument();
  });

  it("labels a reel left by failed overlays and blocks download and corrections", async () => {
    const mock = installMock({ step: 0 });
    const d = mock.state.jobs.get("job-overlays")!.detail;
    Object.assign(d, { status: "error", stage: "error", failed_stage: "overlays" });
    mock.state.jobs.get("job-overlays")!.events = [{ t: "stage", stage: "error" }];
    wrap(<JobPage id="job-overlays" />);
    expect(await screen.findByTestId("result-interim")).toHaveTextContent(t("result.interim"));
    expect(screen.getByRole("button", { name: t("result.download.mp4") })).toBeDisabled();
    expect(screen.getByLabelText(t("result.feedback"))).toBeDisabled();
    expect(screen.getByTestId("fb-locked")).toHaveTextContent(t("lock.interim"));
    await act(async () => undefined);
  });
});
