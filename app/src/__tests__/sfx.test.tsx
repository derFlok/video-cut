import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { I18nProvider, STRINGS } from "../i18n";
import { installMock } from "../mock/server";
import { JobPage } from "../screens/JobPage";
import { SfxSettingsPanel } from "../components/Sfx";

const wrap = (ui: ReactNode, lang: "de" | "en" = "de") => render(<I18nProvider lang={lang}>{ui}</I18nProvider>);
const t = (key: keyof (typeof STRINGS)["de"], lang: "de" | "en" = "de") => STRINGS[lang][key];
const lastCall = (mock: ReturnType<typeof installMock>, path: string) =>
  [...mock.state.calls].reverse().find((c) => c.path === path);

describe("sound effects of a job", () => {
  it("defaults to the settings level and offers cutting again after a change", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<JobPage id="job-done" />);
    const panel = await screen.findByTestId("job-sfx");
    const select = within(panel).getByLabelText(t("sfx.label"));
    expect(select).toHaveValue("default");
    await waitFor(() => expect(within(panel).getByRole("option", { name: "Wie in den Einstellungen (Dezent)" })).toBeInTheDocument());
    expect(within(panel).getByTestId("sfx-desc")).toHaveTextContent(t("sfx.subtle.desc"));
    await user.selectOptions(select, "normal");
    await waitFor(() => expect(lastCall(mock, "/api/jobs/job-done/sfx")!.body).toEqual({ sfx: "normal" }));
    expect(await within(panel).findByTestId("sfx-stale")).toHaveTextContent(t("sfx.stale"));
    expect(within(panel).getByTestId("sfx-desc")).toHaveTextContent(t("sfx.normal.desc"));
  });

  it("is not shown for a job without motion", async () => {
    installMock({ step: 0 });
    wrap(<JobPage id="job-approval" />);
    await screen.findByRole("button", { name: t("plan.approve") });
    expect(screen.queryByTestId("job-sfx")).toBeNull();
  });
});

describe("sound effects in the settings", () => {
  it("changes the default, fetches from Openverse, uploads and removes an own sound", async () => {
    const mock = installMock({ step: 0 });
    const user = userEvent.setup();
    wrap(<SfxSettingsPanel />, "en");
    const panel = await screen.findByTestId("sfx-settings");
    await user.selectOptions(within(panel).getByLabelText(t("sfx.settings.default", "en")), "off");
    await waitFor(() => expect(lastCall(mock, "/api/settings/sfx")!.body).toEqual({ sfx_default: "off" }));

    const pop = () => panel.querySelector('[data-sfx-kind="pop"]') as HTMLElement;
    expect(within(pop()).getByText("Uses: generated locally")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: t("sfx.settings.fetch", "en") }));
    await waitFor(() => expect(within(pop()).getByText("Uses: Openverse (CC0)")).toBeInTheDocument());
    expect(within(pop()).getByText(/CC0 1\.0/)).toBeInTheDocument();

    await user.upload(within(pop()).getByLabelText(t("sfx.settings.upload", "en")), new File([new Uint8Array(8)], "pop.wav"));
    await waitFor(() => expect(within(pop()).getByText("Uses: own sound")).toBeInTheDocument());
    const own = pop().querySelector('[data-sfx-sound^="up-pop"]') as HTMLElement;
    await user.click(within(own).getByRole("button", { name: t("sfx.settings.remove", "en") }));
    await waitFor(() => expect(within(pop()).getByText("Uses: Openverse (CC0)")).toBeInTheDocument());
  });
});
