// Closed-form step response of a damped spring (BRIEF §8): position from 0 towards 1
// after `tau` seconds, natural frequency `w` (rad/s), damping ratio `z`.
// Pure and stateless, so any frame can be drawn on its own.

function step(tau, w = 13, z = 0.8) {
  if (!(tau > 0)) return 0;
  if (!(w > 0)) return 1;
  z = Math.max(0, z);
  if (z === 1) return 1 - Math.exp(-w * tau) * (1 + w * tau);
  if (z > 1) {
    // Overdamped: two real roots. (The critical formula would be wrong here.)
    const s = w * Math.sqrt(z * z - 1);
    const r1 = -z * w + s;
    const r2 = -z * w - s;
    return 1 - (r2 * Math.exp(r1 * tau) - r1 * Math.exp(r2 * tau)) / (r2 - r1);
  }
  const wd = w * Math.sqrt(1 - z * z);
  return 1 - Math.exp(-z * w * tau) * (Math.cos(wd * tau) + ((z * w) / wd) * Math.sin(wd * tau));
}

// Spring parameters of a brand, with the BRIEF defaults when the brand has none.
function brandSpring(tokens) {
  const s = (tokens && tokens.motion && tokens.motion.spring) || {};
  return { w: Number.isFinite(s.w) ? s.w : 13, z: Number.isFinite(s.z) ? s.z : 0.8 };
}

module.exports = { step, brandSpring };
