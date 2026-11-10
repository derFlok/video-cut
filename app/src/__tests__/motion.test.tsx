import { render, screen, waitFor, within } from "@testing-library/react";
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

describe("motion in the new-job form", () => {
  it("is off by default and sends nothing then", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    const group = await screen.findByTestId("new-motion");
    expect(within(group).getByRole("radio", { name: t("motion.mode.off") })).toBeChecked();
    await user.click(screen.getByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs")).toBeTruthy());
    const form = lastCall(mock, "/api/jobs")!.body as FormData;
    expect(form.get("motion_mode")).toBeNull();
  });

  it("a wish needs text; mode and wish are sent", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />);
    await user.click(await screen.findByText("Gedanke des Tages"));
    await user.upload(screen.getByLabelText(/Kamera-Aufnahme/), file("take.mov", 200));
    const group = screen.getByTestId("new-motion");
    await user.click(within(group).getByRole("radio", { name: t("motion.mode.wish") }));
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    expect(await screen.findByText(t("motion.err.nowish"))).toBeInTheDocument();
    expect(lastCall(mock, "/api/jobs")).toBeUndefined();
    await user.type(within(group).getByLabelText(t("motion.wish")), "Zahlen als Zähler");
    await user.click(screen.getByRole("button", { name: t("new.submit") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs")).toBeTruthy());
    const form = lastCall(mock, "/api/jobs")!.body as FormData;
    expect(form.get("motion_mode")).toBe("wish");
    expect(form.get("motion_wish")).toBe("Zahlen als Zähler");
  });
});

describe("motion moments at the plan gate", () => {
  it("shows each moment with its still, components, what it shows and the beats", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-approval" />);
    const cards = await screen.findByTestId("plan-motion");
    const card = cards.querySelector('[data-motion="m1"]') as HTMLElement;
    expect(within(card).getByText(t("motion.comp.checklist"))).toBeInTheDocument();
    expect(within(card).getByText(/Die drei Schritte haken sich ab/)).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: t("motion.plan.still") })).toBeInTheDocument();
    const beats = within(card).getByTestId("beats-m1");
    expect(within(beats).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "a1: „Anfrage“",
      "a2: „prüfen“",
      `a3: „antworten“ ${t("motion.beat.end")}`,
    ]);
  });

  it("asks Claude to change one moment through plan feedback", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    const card = (await screen.findByTestId("plan-motion")).querySelector('[data-motion="m1"]') as HTMLElement;
    await user.click(within(card).getByRole("button", { name: t("motion.plan.remove") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/plan/feedback")!.body).toEqual({ text: "Entferne die Animation m1." });
  });
});

describe("motion flags in the result", () => {
  it("names each flagged moment with its words in plain language", async () => {
    const { verifyLines } = await import("../screens/Result");
    const S = STRINGS;
    const tt = ((k: string, v?: Record<string, string | number>) =>
      Object.entries(v ?? {}).reduce((s, [a, b]) => s.replace(`{${a}}`, String(b)), S.en[k as keyof (typeof S)["en"]])) as never;
    const lines = verifyLines({ ok: false, motion_flagged: [{ id: "m1", t0: 4, flags: ["motion_invented"], invented: ["lummerland"] }] } as never, tt);
    expect(lines[0]).toContain(S.en["flag.motion_invented"]);
    expect(lines[0]).toContain("lummerland");
  });
});

describe("motion style in the brand editor", () => {
  it("edits theme, depth, energy and sizes and saves them with the brand", async () => {
    const { BrandEditor } = await import("../screens/Brands");
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<BrandEditor id="dark" />);
    const box = await screen.findByTestId("brand-motion");
    expect(within(box).getByLabelText(t("brands.motion.theme"))).toHaveValue("auto");
    expect(within(box).getByLabelText(t("brands.motion.depth"))).toHaveValue("");
    await user.selectOptions(within(box).getByLabelText(t("brands.motion.theme")), "light");
    await user.selectOptions(within(box).getByLabelText(t("brands.motion.depth")), "flat");
    await user.selectOptions(within(box).getByLabelText(t("brands.motion.energy")), "punchy");
    await user.click(screen.getByRole("button", { name: t("common.save") }));
    await screen.findByText(t("brands.saved"));
    const put = [...mock.state.calls].reverse().find((c) => c.method === "PUT" && c.path === "/api/brands/dark")!;
    const m = (put.body as { motion: Record<string, unknown> }).motion;
    expect(m).toMatchObject({ theme: "light", depth: "flat", energy: "punchy", character: "ruhig" });
  });
});
