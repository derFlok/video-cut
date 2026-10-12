// Full-frame plate scenes (a scene module exporting `plate: true`, e.g. the end card).
// A plate replaces the video, so no caption is drawn while one is on screen.

// The plate items among `items` ({scene, t0, t1}); `scenes` maps names to modules.
function platesOf(items, scenes) {
  return items.filter((it) => scenes[it.scene] && scenes[it.scene].plate);
}

// Whether a caption must stay hidden at output time t.
function hidesCaptions(plates, t) {
  return plates.some((p) => t >= p.t0 && t <= p.t1);
}

module.exports = { platesOf, hidesCaptions };
