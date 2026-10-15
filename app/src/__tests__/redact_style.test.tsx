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

describe("redaction style", () => {
  it("is picked in the approval step next to the personal-data decision", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    await user.click(await screen.findByRole("button", { name: t("plan.approve") }));
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getByLabelText(t("redact.label"));
    expect(select).toHaveValue("auto");
    expect(within(dialog).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Automatisch",
      "Unscharf",
      "Abschneiden",
      "Fläche",
    ]);
    expect(within(dialog).getByTestId("redact-desc")).toHaveTextContent(t("redact.auto.desc"));
    await user.selectOptions(select, "blur");
    expect(within(dialog).getByTestId("redact-desc")).toHaveTextContent(t("redact.blur.desc"));
    await user.click(within(dialog).getByRole("button", { name: t("pd.confirm") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/approve")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/redact-style")!.body).toEqual({ redact_style: "blur" });
    const order = mock.state.calls.map((c) => c.path);
    expect(order.lastIndexOf("/api/jobs/job-approval/redact-style")).toBeLessThan(
      order.lastIndexOf("/api/jobs/job-approval/approve"),
    );
  });

  it("keeps the style untouched when it is not changed, and hides it for demo data", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-approval" />);
    await user.click(await screen.findByRole("button", { name: t("plan.approve") }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: t("pd.mode.demo") }));
    expect(within(dialog).queryByLabelText(t("redact.label"))).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: t("pd.mode.redact_all") }));
    expect(within(dialog).getByLabelText(t("redact.label"))).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: t("pd.confirm") }));
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-approval/approve")).toBeTruthy());
    expect(lastCall(mock, "/api/jobs/job-approval/redact-style")).toBeUndefined();
  });

  it("is sent with a new job that has a screen recording, in English too", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<NewJob />, "en");
    await user.click(await screen.findByText("Product walkthrough"));
    expect(screen.queryByLabelText(t("redact.label", "en"))).not.toBeInTheDocument(); // camera only
    await user.upload(screen.getByLabelText(/Camera recording/), file("take.mov", 200));
    await user.upload(screen.getAllByLabelText(/^Screen recording/)[0], file("screen.mov", 200));
    const select = screen.getByLabelText(t("redact.label", "en"));
    expect(screen.getAllByRole("option", { name: "Cut off" })).toHaveLength(1);
    await user.selectOptions(select, "crop");
    expect(screen.getByTestId("redact-desc")).toHaveTextContent(t("redact.crop.desc", "en"));
    await user.click(screen.getByRole("button", { name: t("new.submit", "en") }));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/jobs\/job-/));
    expect((lastCall(mock, "/api/jobs")!.body as FormData).get("redact_style")).toBe("crop");
  });
});
