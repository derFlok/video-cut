// The motion row of `brand sheet`: four moments in the brand's motion style (theme, depth,
// radius, energy, type scale), neutral sample content. One still per moment at 1.6 s.
export const MOMENTS = 4;
export const SLOT = 2;
export default function scene(vc) {
  const { c, stage } = vc;
  const at = (i, k = 0) => i * SLOT + 0.1 + k;
  const out = (i) => (i + 1) * SLOT - 0.1;
  c.stat(c.slot(stage), { value: 42, at: at(0, 0.2), dur: 0.7, unit: "%", label: "sample", source: "sample source", out: out(0) });
  c.checklist(c.slot(stage), { label: "sample list", items: ["first", "second", "third"].map((text, k) => ({ text, at: at(1, k * 0.15), check: at(1, 0.4 + k * 0.25) })), out: out(1) });
  c.barChart(c.slot(stage), { bars: [3, 5, 8].map((value, k) => ({ label: `${k + 1}`, value, at: at(2, k * 0.2) })), highlight: 2, highlightAt: at(2, 0.9), out: out(2), height: 0.2 });
  c.chips(c.slot(stage), { chips: [["one", "star"], ["two", "bolt"], ["three", "heart"]].map(([text, icon], k) => ({ text, icon, at: at(3, k * 0.2) })), out: out(3) });
}
