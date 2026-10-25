#!/usr/bin/env node
// Upload the site's encoded media (public/media) to Vercel Blob and record where each file went in
// media.manifest.json (committed). The site reads that manifest at build time; files without an entry are served
// from public/media, which is what a local preview uses.
//
//   node scripts/upload-media.mjs              upload what is new or changed, skip the rest
//   node scripts/upload-media.mjs --dry-run    show what would be uploaded; runs nothing, writes nothing
//   node scripts/upload-media.mjs --check      list files that are missing from the manifest or changed since upload
//   options: --uploader vercel|vcup  --link-dir <dir>  --force  --only <text>  --jobs <n> (default 2)
//            --manifest <path> (default media.manifest.json)  --no-verify
//
// Only the files the site uses are uploaded: the ones listed in src/generated/media.json (written by
// scripts/media.py). Anything else in public/media is ignored. A file is skipped when the manifest already has it
// with the same SHA-256, so the script is safe to re-run and resumes after an interruption (the manifest is saved
// after every upload).
//
// Two uploaders. Neither one hands a token to this script: they authenticate themselves.
//
// vercel (default when --link-dir or VERCEL_LINK_DIR is set): the official Vercel CLI, logged in with `vercel login`.
//   vercel blob put <file> --access public --pathname <prefix>/<file name> --cache-control-max-age 31536000
//                          --non-interactive --cwd <link dir>      (+ --allow-overwrite when the file changed)
//   <link dir> is a folder linked (`vercel link`) to the project that owns the Blob store. Pathnames are fixed (no
//   random suffix): <prefix> is BLOB_PREFIX (default project8plus). The URL is read from the CLI output, or derived
//   as <store base>/<pathname> (BLOB_BASE_URL, or learned from an earlier upload). A blob that "already exists" is
//   recorded and skipped, never overwritten unless the manifest says its hash changed. Every URL is checked with a
//   HEAD request (status 200 and the right size) unless --no-verify. Command: VERCEL_CMD (JSON array or a command
//   line), else %APPDATA%\npm\vercel.cmd on Windows when present, else `vercel`.
//
// vcup: github.com/MaxLeiter/vcup, configured by ~/.vcuprc (or VCUP_API_URL / VCUP_TOKEN). Command: VCUP_CMD, else
//   `node ~/.vcup/cli.ts --raw` when that file exists, else `vcup --raw`; the last http(s) line on stdout is the URL.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MEDIA_DIR = path.join(SITE, "public", "media");
const GENERATED = path.join(SITE, "src", "generated", "media.json");
const IS_WIN = process.platform === "win32";

function parseArgs(argv) {
  const a = {
    dryRun: false, check: false, force: false, only: null, jobs: 2, verify: true,
    manifest: path.join(SITE, "media.manifest.json"),
    uploader: process.env.MEDIA_UPLOADER ?? null,
    linkDir: process.env.VERCEL_LINK_DIR ?? null,
  };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--dry-run") a.dryRun = true;
    else if (k === "--check") a.check = true;
    else if (k === "--force") a.force = true;
    else if (k === "--no-verify") a.verify = false;
    else if (k === "--only") a.only = argv[++i];
    else if (k === "--jobs") a.jobs = Math.max(1, Number(argv[++i]) || 1);
    else if (k === "--manifest") a.manifest = path.resolve(argv[++i]);
    else if (k === "--uploader") a.uploader = argv[++i];
    else if (k === "--link-dir") a.linkDir = argv[++i];
    else if (k === "--help" || k === "-h") {
      const src = readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n");
      console.log(src.slice(1, src.findIndex((l) => l.startsWith("import"))).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
      process.exit(0);
    } else {
      console.error(`unknown option ${k} (--help lists them)`);
      process.exit(2);
    }
  }
  a.uploader ??= a.linkDir ? "vercel" : "vcup";
  if (!["vercel", "vcup"].includes(a.uploader)) {
    console.error(`--uploader must be vercel or vcup, not ${a.uploader}`);
    process.exit(2);
  }
  return a;
}

/** Every encoded file the site references, from the media manifest that scripts/media.py writes. */
function referencedFiles() {
  if (!existsSync(GENERATED)) throw new Error(`${path.relative(SITE, GENERATED)} not found: run python scripts/media.py first`);
  const m = JSON.parse(readFileSync(GENERATED, "utf8"));
  const names = new Set();
  for (const img of Object.values(m.images ?? {})) for (const f of img.files) names.add(f.src);
  for (const v of Object.values(m.videos ?? {})) {
    for (const s of v.sources) names.add(s.src);
    for (const s of v.mobile ?? []) names.add(s.src);
    for (const f of v.poster.files) names.add(f.src);
  }
  return [...names].sort();
}

const sha256 = (file) => createHash("sha256").update(readFileSync(file)).digest("hex");

function loadManifest(p) {
  if (!existsSync(p)) return { version: 1, files: {} };
  const m = JSON.parse(readFileSync(p, "utf8"));
  if (m.version !== 1 || typeof m.files !== "object") throw new Error(`${p}: not a version 1 media manifest`);
  return m;
}

function saveManifest(p, m) {
  const sorted = { version: 1, files: Object.fromEntries(Object.entries(m.files).sort(([a], [b]) => a.localeCompare(b))) };
  const tmp = `${p}.tmp`;
  writeFileSync(tmp, JSON.stringify(sorted, null, 1) + "\n");
  renameSync(tmp, p); // atomic: an interrupted run never leaves a half-written manifest
}

/** A command from an env var: a JSON array, or a command line split on spaces. */
function envCommand(name) {
  const v = process.env[name]?.trim();
  if (!v) return null;
  return v.startsWith("[") ? JSON.parse(v) : v.split(/\s+/);
}

/** cmd.exe quoting for .cmd/.bat programs, which Node can only start through a shell. */
const winQuote = (s) => (/^[\w:\\/.=+-]+$/.test(s) ? s : `"${s.replace(/"/g, '""')}"`);

function run(cmd, args, cwd) {
  const viaShell = IS_WIN && /\.(cmd|bat)$/i.test(cmd);
  return new Promise((resolve, reject) => {
    const child = viaShell
      ? spawn([cmd, ...args].map(winQuote).join(" "), { shell: true, cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true })
      : spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, out, err }));
  });
}

const lastLines = (s, n = 3) => s.trim().split(/\r?\n/).slice(-n).join(" | ");

// ------------------------------------------------------------------------------------------------ uploaders
function vcupUploader() {
  const local = path.join(homedir(), ".vcup", "cli.ts");
  const cmd =
    envCommand("VCUP_CMD") ??
    (existsSync(local) ? [process.execPath, local, "--raw"] : [IS_WIN ? "vcup.cmd" : "vcup", "--raw"]);
  return {
    label: `vcup (${cmd.join(" ")})`,
    async upload(p) {
      const r = await run(cmd[0], [...cmd.slice(1), p.file]);
      const url = r.out.split(/\r?\n/).map((l) => l.trim()).filter((l) => /^https?:\/\/\S+$/.test(l)).pop();
      if (r.code !== 0 || !url) throw new Error(`vcup exited ${r.code}${url ? "" : ", no URL on stdout"}: ${lastLines(r.err || r.out)}`);
      return { url };
    },
  };
}

function vercelUploader(a) {
  if (!a.linkDir) throw new Error("the vercel uploader needs --link-dir <dir> or VERCEL_LINK_DIR (a folder linked to the project with the Blob store)");
  const linkDir = path.resolve(a.linkDir);
  if (!existsSync(linkDir)) throw new Error(`link dir not found: ${linkDir}`);
  const appdataCli = process.env.APPDATA ? path.join(process.env.APPDATA, "npm", "vercel.cmd") : null;
  const cmd = envCommand("VERCEL_CMD") ?? [appdataCli && existsSync(appdataCli) ? appdataCli : IS_WIN ? "vercel.cmd" : "vercel"];
  const prefix = (process.env.BLOB_PREFIX ?? "project8plus").replace(/^\/+|\/+$/g, "");
  let base = process.env.BLOB_BASE_URL?.replace(/\/+$/, "") || null;
  const learnBase = (url, pathname) => {
    if (!base && url.endsWith(`/${pathname}`)) base = url.slice(0, -pathname.length - 1);
  };
  return {
    label: `vercel blob put (${cmd.join(" ")}), --cwd ${linkDir}, pathname ${prefix}/<file>`,
    prefix,
    learn(manifest) {
      for (const [name, e] of Object.entries(manifest.files)) learnBase(e.url, `${prefix}/${name}`);
    },
    async upload(p, { overwrite }) {
      const pathname = `${prefix}/${p.name}`;
      const args = [...cmd.slice(1), "blob", "put", p.file, "--access", "public", "--pathname", pathname,
        "--cache-control-max-age", "31536000", "--non-interactive", "--cwd", linkDir];
      if (overwrite) args.push("--allow-overwrite");
      const r = await run(cmd[0], args, linkDir);
      const text = `${r.out}\n${r.err}`;
      const urls = [...text.matchAll(/https?:\/\/[^\s"'<>)`]+/g)].map((m) => m[0].replace(/[.,;]+$/, ""));
      const url = urls.find((u) => u.endsWith(`/${pathname}`)) ?? null;
      if (url) learnBase(url, pathname);
      if (r.code === 0) {
        const u = url ?? (base ? `${base}/${pathname}` : null);
        if (!u) throw new Error(`uploaded, but no URL in the CLI output and no BLOB_BASE_URL to derive it from: ${lastLines(text)}`);
        return { url: u };
      }
      if (/already exists/i.test(text)) {
        const u = url ?? (base ? `${base}/${pathname}` : null);
        if (!u) throw new Error(`blob ${pathname} already exists, and its URL is unknown: set BLOB_BASE_URL (the store's https://<id>.public.blob.vercel-storage.com) and run again`);
        return { url: u, existed: true };
      }
      throw new Error(`vercel exited ${r.code}: ${lastLines(text)}`);
    },
  };
}

/** HEAD the URL: 200 and the local file's size, else throw. */
async function verify(url, bytes) {
  const res = await fetch(url, { method: "HEAD", redirect: "follow" });
  if (!res.ok) throw new Error(`HEAD ${url} -> ${res.status}`);
  const len = Number(res.headers.get("content-length"));
  if (len && len !== bytes) throw new Error(`HEAD ${url}: ${len} bytes online, ${bytes} locally (a different file exists at this pathname)`);
}

const fmt = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MiB` : `${Math.round(n / 1024)} KiB`);

async function main() {
  const a = parseArgs(process.argv.slice(2));
  const manifest = loadManifest(a.manifest);
  let names = referencedFiles();
  if (a.only) names = names.filter((n) => n.includes(a.only));
  const missing = names.filter((n) => !existsSync(path.join(MEDIA_DIR, n)));
  if (missing.length) {
    console.error(`${missing.length} referenced file(s) are not in public/media (run python scripts/media.py): ${missing.slice(0, 5).join(", ")}`);
    process.exit(1);
  }

  const plan = names.map((name) => {
    const file = path.join(MEDIA_DIR, name);
    const hash = sha256(file);
    const bytes = statSync(file).size;
    const have = manifest.files[name];
    const state = !have ? "new" : have.sha256 !== hash ? "changed" : "uploaded";
    return { name, file, hash, bytes, state };
  });
  const todo = plan.filter((p) => a.force || p.state !== "uploaded");
  const unused = a.only ? [] : Object.keys(manifest.files).filter((n) => !names.includes(n));

  console.log(`manifest  ${path.relative(process.cwd(), a.manifest) || a.manifest}`);
  console.log(`files     ${plan.length} referenced, ${plan.length - todo.length} already uploaded, ${todo.length} to upload (${fmt(todo.reduce((s, p) => s + p.bytes, 0))})`);
  if (unused.length) console.log(`note      ${unused.length} manifest entr${unused.length === 1 ? "y is" : "ies are"} no longer used by the site (kept): ${unused.slice(0, 4).join(", ")}${unused.length > 4 ? ", ..." : ""}`);

  if (a.check) {
    for (const p of plan.filter((x) => x.state !== "uploaded")) console.log(`  ${p.state.padEnd(8)} ${p.name}`);
    process.exit(todo.length ? 1 : 0);
  }
  if (a.dryRun) {
    const pre = a.uploader === "vercel" ? `${(process.env.BLOB_PREFIX ?? "project8plus").replace(/^\/+|\/+$/g, "")}/` : "";
    console.log(`uploader  ${a.uploader}${a.uploader === "vercel" ? ` (link dir ${a.linkDir ?? "NOT SET: --link-dir or VERCEL_LINK_DIR"})` : ""}`);
    for (const p of todo) console.log(`  would upload  ${(pre + p.name).padEnd(50)} ${fmt(p.bytes).padStart(9)}  (${p.state}${p.state === "changed" && a.uploader === "vercel" ? ", overwrite" : ""})`);
    console.log("dry run: nothing was uploaded and the manifest was not changed");
    return;
  }
  if (!todo.length) {
    console.log("nothing to do");
    return;
  }

  const up = a.uploader === "vercel" ? vercelUploader(a) : vcupUploader();
  up.learn?.(manifest);
  console.log(`uploader  ${up.label}`);
  let done = 0;
  let skipped = 0;
  let failed = 0;
  let next = 0;
  const worker = async () => {
    while (next < todo.length) {
      const p = todo[next++];
      const t0 = Date.now();
      const n = () => `[${done + skipped + failed}/${todo.length}]`;
      try {
        // overwrite only what this manifest says has changed (or everything with --force)
        const r = await up.upload(p, { overwrite: a.force || p.state === "changed" });
        if (a.verify) await verify(r.url, p.bytes);
        manifest.files[p.name] = { url: r.url, sha256: p.hash, bytes: p.bytes, uploaded: new Date().toISOString() };
        saveManifest(a.manifest, manifest);
        if (r.existed) skipped++;
        else done++;
        console.log(`  ${n()} ${p.name.padEnd(36)} ${fmt(p.bytes).padStart(9)}  ${r.existed ? "already there, recorded" : `${((Date.now() - t0) / 1000).toFixed(1)} s`}  ${r.url}`);
      } catch (e) {
        failed++;
        console.error(`  ${n()} ${p.name.padEnd(36)} FAILED  ${e.message}`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(a.jobs, todo.length) }, worker));
  console.log(`${done} uploaded, ${skipped} already there, ${failed} failed${failed ? ": run the script again to retry them" : ""}`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e.message ?? e);
  process.exit(1);
});
