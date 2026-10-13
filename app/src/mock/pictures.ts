// Placeholder pictures for the mock (SVG data URLs): neutral greys, no real content.

const svg = (w: number, h: number, body: string) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`,
  )}`;

const SIZES: Record<string, [number, number]> = { "9:16": [180, 320], "16:9": [320, 180], "1:1": [240, 240], "4:5": [240, 300] };

/** A person in front of a wall (camera still). */
export function personStill(seed: number, format = "16:9"): string {
  const [w, h] = SIZES[format] ?? SIZES["16:9"];
  const shade = 70 + ((seed * 37) % 60);
  const cx = w * (0.4 + ((seed * 13) % 20) / 100);
  return svg(
    w,
    h,
    `<rect width="${w}" height="${h}" fill="rgb(${shade},${shade + 6},${shade + 12})"/>` +
      `<rect x="${w * 0.62}" y="${h * 0.12}" width="${w * 0.25}" height="${h * 0.3}" fill="rgba(255,255,255,0.12)"/>` +
      `<circle cx="${cx}" cy="${h * 0.42}" r="${Math.min(w, h) * 0.16}" fill="#c9b8a6"/>` +
      `<ellipse cx="${cx}" cy="${h * 1.02}" rx="${Math.min(w, h) * 0.38}" ry="${h * 0.38}" fill="#3c4650"/>`,
  );
}

/** A generic app screen with rows (screen still, personal-data candidates). */
export function screenStill(seed: number): string {
  const w = 320;
  const h = 200;
  let rows = "";
  for (let i = 0; i < 6; i++) {
    const y = 46 + i * 24;
    rows += `<rect x="20" y="${y}" width="${120 + ((seed + i) * 23) % 120}" height="10" rx="3" fill="#c8ccd2"/>`;
    rows += `<rect x="250" y="${y}" width="50" height="10" rx="3" fill="#dde0e4"/>`;
  }
  return svg(
    w,
    h,
    `<rect width="${w}" height="${h}" fill="#f2f3f5"/><rect width="${w}" height="28" fill="#d9dce1"/>` +
      `<circle cx="14" cy="14" r="4" fill="#aab0b8"/><circle cx="28" cy="14" r="4" fill="#aab0b8"/>${rows}`,
  );
}

export function coverPicture(format = "9:16"): string {
  const [w, h] = SIZES[format] ?? SIZES["9:16"];
  return svg(
    w,
    h,
    `<rect width="${w}" height="${h}" fill="#2f353b"/>` +
      `<circle cx="${w / 2}" cy="${h * 0.45}" r="${Math.min(w, h) * 0.18}" fill="#b9a895"/>` +
      `<ellipse cx="${w / 2}" cy="${h}" rx="${w * 0.42}" ry="${h * 0.32}" fill="#4a535c"/>` +
      `<rect x="${w * 0.12}" y="${h * 0.12}" width="${w * 0.76}" height="${h * 0.12}" rx="6" fill="#f4f4f4"/>`,
  );
}

/** A preview sheet from the brand colours: tiles in the format, caption bar, card, accent. */
export function brandSheet(colors: Record<string, string>, format: string): string {
  const [tw, th] = SIZES[format] ?? SIZES["9:16"];
  const bg = colors.bg ?? "#F4F4F4";
  const surface = colors.surface ?? "#FFFFFF";
  const text = colors.text ?? "#141414";
  const accent = colors.accent ?? "#141414";
  const muted = colors.muted ?? "#6B6B6B";
  const pad = 10;
  const W = tw * 3 + pad * 4;
  const H = th + pad * 2;
  let tiles = "";
  for (let i = 0; i < 3; i++) {
    const x = pad + i * (tw + pad);
    tiles += `<g transform="translate(${x},${pad})"><rect width="${tw}" height="${th}" fill="${bg}"/>`;
    if (i === 0)
      tiles += `<rect x="${tw * 0.12}" y="${th * 0.62}" width="${tw * 0.76}" height="${th * 0.1}" rx="4" fill="${surface}"/><rect x="${tw * 0.18}" y="${th * 0.655}" width="${tw * 0.4}" height="${th * 0.03}" fill="${text}"/><rect x="${tw * 0.6}" y="${th * 0.655}" width="${tw * 0.2}" height="${th * 0.03}" fill="${accent}"/>`;
    if (i === 1)
      tiles += `<rect x="${tw * 0.15}" y="${th * 0.3}" width="${tw * 0.7}" height="${th * 0.36}" rx="8" fill="${surface}"/><text x="${tw / 2}" y="${th * 0.52}" font-family="system-ui" font-size="${Math.min(tw, th) * 0.2}" font-weight="800" text-anchor="middle" fill="${accent}">30 %</text><rect x="${tw * 0.3}" y="${th * 0.57}" width="${tw * 0.4}" height="${th * 0.025}" fill="${muted}"/>`;
    if (i === 2)
      tiles += `<rect x="${tw * 0.1}" y="${th * 0.4}" width="${tw * 0.8}" height="${th * 0.2}" rx="${th * 0.1}" fill="${accent}"/><rect x="${tw * 0.25}" y="${th * 0.48}" width="${tw * 0.5}" height="${th * 0.04}" fill="${bg}"/>`;
    tiles += "</g>";
  }
  return svg(W, H, `<rect width="${W}" height="${H}" fill="#8a8f96"/>${tiles}`);
}
