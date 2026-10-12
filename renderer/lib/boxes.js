// Bounding boxes [x0, y0, x1, y1]: union, and the per-item collection behind --boxes
// (the area every scene or caption group actually drew over its lifetime).

function union(a, b) {
  if (!a) return b ? b.slice() : null;
  if (!b) return a.slice();
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

class BoxCollector {
  constructor(size) {
    this.size = size;
    this.items = new Map();
  }

  // Record that item `key` ({scene, t0, t1}) drew inside `box` on some frame.
  add(key, meta, box) {
    if (!box) return;
    const prev = this.items.get(key);
    this.items.set(key, { ...meta, box: union(prev && prev.box, box) });
  }

  // Integer boxes clipped to the frame, in first-drawn order.
  list() {
    const [w, h] = this.size;
    return [...this.items.values()].map(({ scene, t0, t1, box }) => ({
      scene,
      t0,
      t1,
      box: [
        Math.max(0, Math.floor(box[0])),
        Math.max(0, Math.floor(box[1])),
        Math.min(w, Math.ceil(box[2])),
        Math.min(h, Math.ceil(box[3])),
      ],
    }));
  }
}

module.exports = { union, BoxCollector };
