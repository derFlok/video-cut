// Neutral gallery of the five motion components (renderer test fixture).
export default function scene(vc) {
  const { c, stage } = vc;
  stage.style.display = "flex";
  stage.style.flexDirection = "column";
  stage.style.gap = `${Math.round(vc.unit * 0.03)}px`;
  stage.style.alignItems = "center";
  c.headline(stage, {
    words: ["one", "small", "step", "every", "word"].map((text, i) => ({ text, at: vc.at(text) })),
    mark: [{ index: 2, at: vc.beat("mark"), style: "box" }, { index: 4, at: vc.end("word"), style: "underline" }],
    size: 0.07,
  });
  c.counter(stage, { from: 0, to: 128, at: vc.beat("count"), suffix: "items", label: "counted" });
  c.checklist(stage, {
    label: "list",
    items: [
      { text: "first item", at: 0.4, check: vc.at("first") },
      { text: "second item", at: 0.5, check: vc.at("second") },
    ],
  });
  c.strikeCard(stage, { label: "before", text: "old way", at: 0.6, strikeAt: vc.at("old"), then: "new way" });
  c.terminal(stage, { lines: [{ text: "make things", at: 0.8 }, { text: "done in 3 steps", at: 1.6, out: true }], width: 0.8 });
}
