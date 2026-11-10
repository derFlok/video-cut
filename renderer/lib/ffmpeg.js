// ffmpeg encoder for rendered frames (PNG on stdin -> ProRes with or without alpha),
// shared by render.js and motion.js.
const { spawn } = require("child_process");

// ffmpeg rate argument: NTSC-style rates (29.97, 59.94, 23.976) as exact rationals.
function rate(fps) {
  const n = Math.round((fps * 1001) / 1000);
  if (!Number.isInteger(fps) && Math.abs((n * 1000) / 1001 - fps) < 0.005) return `${n * 1000}/1001`;
  return String(fps);
}

function startFfmpeg(out, size, fps, opaque = false, encode = null) {
  // An opaque slide clip: ProRes 422 HQ without alpha; an overlay: ProRes 4444 with alpha.
  const codec = opaque ? ["-profile:v", "3", "-pix_fmt", "yuv422p10le"] : ["-profile:v", "4444", "-pix_fmt", "yuva444p10le"];
  const ff = spawn(
    "ffmpeg",
    [
      "-y", "-loglevel", "error",
      "-f", "image2pipe", "-framerate", rate(fps), "-c:v", "png", "-i", "-",
      ...(encode || [
        // BT.709 limited range, tagged: render composites it into a BT.709 reel (an
        // untagged RGB -> YUV conversion would use BT.601 and shift the brand colours).
        "-vf", `scale=${size[0]}:${size[1]}:out_color_matrix=bt709:out_range=tv`,
        "-c:v", "prores_ks", ...codec,
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
      ]),
      out,
    ],
    { stdio: ["pipe", "ignore", "pipe"] },
  );
  let err = "";
  let failed = null;
  ff.stderr.on("data", (d) => (err += d));
  // A broken pipe shows up as ffmpeg's exit below; the error itself carries nothing new.
  ff.stdin.on("error", () => {});
  const done = new Promise((res, rej) => {
    ff.on("error", (e) => rej((failed = e)));
    ff.on("close", (code) => {
      if (code === 0) return res();
      rej((failed = new Error(`ffmpeg exited ${code}: ${err.trim().slice(-400)}`)));
    });
  });
  done.catch(() => {}); // surfaced by write() or end()
  const write = async (buf) => {
    if (failed) throw failed;
    if (!ff.stdin.write(buf)) await Promise.race([new Promise((r) => ff.stdin.once("drain", r)), done]);
    if (failed) throw failed;
  };
  return { write, end: () => (ff.stdin.end(), done), kill: () => ff.kill("SIGKILL") };
}

// The glass mask (lib/glass.js): PNG frames (white plates on transparent) -> their alpha as
// gray, lossless FFV1 (mostly empty frames: a few kB each).
function startMask(out, size, fps) {
  return startFfmpeg(out, size, fps, false, ["-vf", "alphaextract,format=gray", "-c:v", "ffv1", "-level", "3", "-g", "1"]);
}

module.exports = { rate, startFfmpeg, startMask };
