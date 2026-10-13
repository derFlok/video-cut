import type { Format } from "../api/types";

const BOX: Record<Format, [number, number]> = { "9:16": [9, 16], "16:9": [16, 9], "1:1": [12, 12], "4:5": [11, 13.75] };

/** A small outlined rectangle in the aspect of the format. */
export function AspectIcon({ format, size = 18 }: { format: Format | "auto"; size?: number }) {
  if (format === "auto") {
    return (
      <svg width={size} height={size} viewBox="0 0 18 18" aria-hidden="true" className="aspect-icon">
        <rect x="5.5" y="1.5" width="7" height="12" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <rect x="1.5" y="8.5" width="12" height="7" rx="1" fill="none" stroke="currentColor" strokeWidth="1.3" strokeDasharray="2 1.5" />
      </svg>
    );
  }
  const [w, h] = BOX[format];
  const k = 16 / Math.max(w, h);
  const W = w * k;
  const H = h * k;
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" aria-hidden="true" className="aspect-icon">
      <rect x={(18 - W) / 2} y={(18 - H) / 2} width={W} height={H} rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
