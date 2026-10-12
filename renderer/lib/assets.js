// Node side only (never required by a scene): resolve, check and read the image files
// scenes declare through `assets(item, tokens)`. Overlay specs are untrusted input, so
// a "job" file must resolve (symlinks followed) to a file inside the job folder, and a
// "brand" file (a logo) inside the job folder or a brands folder. Only image types.

const fs = require("fs");
const path = require("path");

const MIME = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml" };
const EXT = { job: [".png", ".jpg", ".jpeg", ".webp"], brand: [".svg", ".png"] };
const MAX_BYTES = 50 * 1024 * 1024;

const inside = (file, dir) => {
  const rel = path.relative(dir, file);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
};

const real = (p) => {
  try {
    return fs.realpathSync(p);
  } catch {
    return null;
  }
};

// {ref, root} -> {ref, mime, b64}. `roots.job` is the job folder, `roots.brands` a list
// of brands folders. Throws with a message naming the reference when it is refused.
function readAsset({ ref, root }, roots) {
  if (typeof ref !== "string" || !ref || ref.includes("\0")) throw new Error(`asset ${JSON.stringify(ref)}: not a file path`);
  if (/^[a-z][a-z0-9+.-]*:/i.test(ref) && !ref.startsWith("file://"))
    throw new Error(`asset ${ref}: only files are allowed`);
  const raw = ref.startsWith("file://") ? decodeURIComponent(new URL(ref).pathname) : ref;
  const file = real(path.resolve(roots.job, raw));
  if (!file) throw new Error(`asset ${ref}: file not found`);
  const allowed = [roots.job, ...(root === "brand" ? roots.brands || [] : [])].map(real).filter(Boolean);
  if (!allowed.some((d) => inside(file, d)))
    throw new Error(`asset ${ref}: outside the ${root === "brand" ? "job and brands folders" : "job folder"}`);
  const ext = path.extname(file).toLowerCase();
  if (!(EXT[root] || EXT.job).includes(ext)) throw new Error(`asset ${ref}: ${ext || "no extension"} is not an allowed image type`);
  const st = fs.statSync(file);
  if (!st.isFile() || st.size > MAX_BYTES) throw new Error(`asset ${ref}: not a regular file of at most 50 MB`);
  return { ref, mime: MIME[ext], b64: fs.readFileSync(file).toString("base64") };
}

// Every asset the items need, each read once, keyed by its reference.
function collectAssets(list, scenes, tokens, roots) {
  const out = new Map();
  const seen = new Set();
  for (const it of list) {
    const scene = scenes[it.scene];
    if (!scene || typeof scene.assets !== "function") continue;
    for (const a of scene.assets(it, tokens) || []) {
      // Each (reference, root) pair is checked; the bytes are read once per reference.
      const key = `${a.root}:${a.ref}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const read = readAsset(a, roots);
      if (!out.has(a.ref)) out.set(a.ref, read);
    }
  }
  return [...out.values()];
}

module.exports = { readAsset, collectAssets };
