const test = require("node:test");
const assert = require("node:assert/strict");
const { step, brandSpring } = require("../lib/spring");

// Numerical reference: x'' = w^2 (1 - x) - 2 z w x', x(0) = x'(0) = 0 (RK4).
function simulate(tau, w, z, dt = 1e-4) {
  let x = 0;
  let v = 0;
  const acc = (x, v) => w * w * (1 - x) - 2 * z * w * v;
  for (let t = 0; t < tau - 1e-12; t += dt) {
    const h = Math.min(dt, tau - t);
    const k1x = v, k1v = acc(x, v);
    const k2x = v + (h / 2) * k1v, k2v = acc(x + (h / 2) * k1x, v + (h / 2) * k1v);
    const k3x = v + (h / 2) * k2v, k3v = acc(x + (h / 2) * k2x, v + (h / 2) * k2v);
    const k4x = v + h * k3v, k4v = acc(x + h * k3x, v + h * k3v);
    x += (h / 6) * (k1x + 2 * k2x + 2 * k3x + k4x);
    v += (h / 6) * (k1v + 2 * k2v + 2 * k3v + k4v);
  }
  return x;
}

test("starts at 0 and is 0 before the start", () => {
  assert.equal(step(0), 0);
  assert.equal(step(-1), 0);
  assert.equal(step(NaN), 0);
});

test("matches the differential equation for under-, critically and overdamped springs", () => {
  for (const z of [0, 0.3, 0.8, 0.999, 1, 1.001, 1.5, 3]) {
    for (const tau of [0.02, 0.1, 0.25, 0.6, 1.5]) {
      const got = step(tau, 13, z);
      const want = simulate(tau, 13, z);
      assert.ok(Math.abs(got - want) < 1e-6, `z=${z} tau=${tau}: ${got} vs ${want}`);
    }
  }
});

test("continuous across critical damping (the fix)", () => {
  for (const tau of [0.05, 0.2, 0.5]) {
    const a = step(tau, 13, 1 - 1e-6);
    const b = step(tau, 13, 1);
    const c = step(tau, 13, 1 + 1e-6);
    assert.ok(Math.abs(a - b) < 1e-4 && Math.abs(c - b) < 1e-4, `${a} ${b} ${c}`);
  }
});

test("settles at 1; only underdamped springs overshoot", () => {
  assert.ok(Math.abs(step(5, 13, 0.8) - 1) < 1e-9);
  const peak = (z) => Math.max(...Array.from({ length: 400 }, (_, i) => step(i / 200, 13, z)));
  assert.ok(peak(0.5) > 1.05);
  assert.ok(peak(1) <= 1);
  assert.ok(peak(2) <= 1);
});

test("brand spring falls back to the BRIEF defaults", () => {
  assert.deepEqual(brandSpring({}), { w: 13, z: 0.8 });
  assert.deepEqual(brandSpring({ motion: { spring: { w: 20, z: 1 } } }), { w: 20, z: 1 });
});
