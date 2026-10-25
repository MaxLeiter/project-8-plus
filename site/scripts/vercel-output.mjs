#!/usr/bin/env node
// Turn the static export (out/, from `next build`) into a Vercel Build Output API v3 folder (.vercel/output) for
// `vercel deploy --prebuilt`. This replaces `vercel build`: its Next.js builder maps the exported pages to extension-
// less paths and, with trailingSlash, then redirects "/index" to "/index/", so "/" served the 404 page.
// Here the export is served as plain static files: "/" is index.html, headers come from vercel.json + /media below.
//
//   npm run deploy            (= scripts/deploy.mjs: build, this script, vercel deploy --prebuilt --prod)
//   node scripts/vercel-output.mjs          after `npm run build`
//   node scripts/vercel-output.mjs --blob   the opt-in Blob route (pages built with MEDIA_SOURCE=blob): no /media
//
// Default (self-contained): the media ships inside the deployment. Every built file (html, the RSC .txt payloads,
// js, css, json) is scanned for /media/<file> references (src, srcset, poster, lightbox JSON). Only those files are
// copied, each renamed to <name>.<sha256-8><ext>, and every reference is rewritten to the new name. Because the
// names change when the content does, /media/* is served with a one-year immutable cache.
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(SITE, "out");
const DEST = path.join(SITE, ".vercel", "output");
const STATIC = path.join(DEST, "static");
const BLOB = process.argv.includes("--blob");
const basePath = (process.env.BASE_PATH ?? "").replace(/\/$/, "");
const TEXT = new Set([".html", ".txt", ".js", ".css", ".json", ".xml", ".svg", ".webmanifest"]);

if (!existsSync(path.join(OUT, "index.html"))) {
  console.error("out/index.html not found: run `npm run build` first");
  process.exit(1);
}

function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) files.push(...walk(p));
    else files.push(p);
  }
  return files;
}

const outFiles = walk(OUT).filter((f) => path.relative(OUT, f).split(path.sep)[0] !== "media");
const textFiles = outFiles.filter((f) => TEXT.has(path.extname(f).toLowerCase()));
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// (?<!static): not /_next/static/media/ (fonts and other bundler assets)
const REF = new RegExp(`(?<!static)${esc(basePath)}/media/([A-Za-z0-9_-][A-Za-z0-9._-]*\\.(?:avif|webp|jpe?g|png|gif|mp4|webm|vtt))`, "g");

// which media files do the built pages reference?
const referenced = new Set();
for (const f of textFiles) for (const m of readFileSync(f, "utf8").matchAll(REF)) referenced.add(m[1]);

if (BLOB && referenced.size) {
  console.error(`--blob: the pages still link ${referenced.size} /media/ file(s), e.g. ${[...referenced][0]}: build with MEDIA_SOURCE=blob after uploading (README: Deploying)`);
  process.exit(1);
}

// content-hashed names
const rename = new Map();
const missing = [];
for (const name of referenced) {
  const src = path.join(OUT, "media", name);
  if (!existsSync(src)) {
    missing.push(name);
    continue;
  }
  const hash = createHash("sha256").update(readFileSync(src)).digest("hex").slice(0, 8);
  const ext = path.extname(name);
  rename.set(name, `${name.slice(0, -ext.length)}.${hash}${ext}`);
}
if (missing.length) {
  console.error(`${missing.length} referenced media file(s) are not in out/media (run python scripts/media.py): ${missing.slice(0, 5).join(", ")}`);
  process.exit(1);
}

const vercelJson = JSON.parse(readFileSync(path.join(SITE, "vercel.json"), "utf8"));
const routes = [
  ...(vercelJson.headers ?? []).map((h) => ({
    src: h.source,
    headers: Object.fromEntries(h.headers.map((x) => [x.key, x.value])),
    continue: true,
  })),
  ...(BLOB
    ? []
    : [{ src: `${basePath}/media/(.*)`, headers: { "Cache-Control": "public, max-age=31536000, immutable" }, continue: true }]),
  // Next's own build assets are content-hashed too
  { src: `${basePath}/_next/static/(.*)`, headers: { "Cache-Control": "public, max-age=31536000, immutable" }, continue: true },
  { handle: "filesystem" },
  { src: "/(.*)", status: 404, dest: "/404.html" },
];

rmSync(DEST, { recursive: true, force: true });
mkdirSync(STATIC, { recursive: true });
for (const f of outFiles) {
  const rel = path.relative(OUT, f);
  const to = path.join(STATIC, rel);
  mkdirSync(path.dirname(to), { recursive: true });
  if (!BLOB && TEXT.has(path.extname(f).toLowerCase())) {
    const s = readFileSync(f, "utf8").replace(REF, (all, name) => `${basePath}/media/${rename.get(name)}`);
    writeFileSync(to, s);
  } else cpSync(f, to);
}
let mediaBytes = 0;
if (!BLOB) {
  mkdirSync(path.join(STATIC, "media"), { recursive: true });
  for (const [name, hashed] of rename) {
    cpSync(path.join(OUT, "media", name), path.join(STATIC, "media", hashed));
    mediaBytes += statSync(path.join(OUT, "media", name)).size;
  }
}
writeFileSync(path.join(DEST, "config.json"), JSON.stringify({ version: 3, routes }, null, 2) + "\n");

const all = walk(STATIC);
const total = all.reduce((n, f) => n + statSync(f).size, 0);
const mb = (b) => (b / 1048576).toFixed(1) + " MiB";
const available = existsSync(path.join(OUT, "media")) ? readdirSync(path.join(OUT, "media")).length : 0;
console.log(
  `wrote ${path.relative(SITE, DEST)}: ${all.length} files, ${mb(total)}` +
    (BLOB ? " (no /media: Blob route)" : `; media ${rename.size} of ${available} files in public/media, ${mb(mediaBytes)}`),
);
