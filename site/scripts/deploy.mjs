#!/usr/bin/env node
// One command for a deploy of the self-contained site (media inside the deployment, no Blob, no tokens):
//
//   npm run deploy           build -> .vercel/output -> vercel deploy --prebuilt --prod   (production)
//   npm run deploy:preview   the same without --prod (a preview URL; production untouched)
//
// Needs the Vercel CLI logged in (`vercel login`) and this folder linked to the project (.vercel/project.json;
// `vercel link --project project8plus --scope maxleiters-team` creates it). The script never handles a token.
// Prints the deployment URL at the end. Roll back with `vercel rollback <previous deployment> --scope maxleiters-team`.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCOPE = process.env.VERCEL_SCOPE ?? "maxleiters-team";
const prod = process.argv.includes("--prod");

function run(cmd, args, opts = {}) {
  console.log(`\n> ${[cmd, ...args].join(" ")}`);
  const r = spawnSync(cmd, args, { cwd: SITE, stdio: opts.capture ? ["inherit", "pipe", "inherit"] : "inherit", shell: process.platform === "win32", encoding: "utf8", env: opts.env ?? process.env });
  if (opts.capture && r.stdout) process.stdout.write(r.stdout);
  if (r.status !== 0) {
    console.error(`\nfailed: ${cmd} ${args.join(" ")} (exit ${r.status ?? r.error?.message})`);
    process.exit(1);
  }
  return r.stdout ?? "";
}

const link = path.join(SITE, ".vercel", "project.json");
if (!existsSync(link)) {
  console.error(`not linked: run \`vercel link --project project8plus --scope ${SCOPE}\` in ${SITE} first`);
  process.exit(1);
}
const project = JSON.parse(readFileSync(link, "utf8")).projectName ?? "?";

const appdataVercel = process.env.APPDATA ? path.join(process.env.APPDATA, "npm", "vercel.cmd") : "";
const vercel = process.env.VERCEL_CMD || (appdataVercel && existsSync(appdataVercel) ? `"${appdataVercel}"` : "vercel");

// the default build serves media from the same origin; make sure a leftover MEDIA_SOURCE=blob does not leak in
const env = { ...process.env };
delete env.MEDIA_SOURCE;
run("npm", ["run", "build"], { env });
run("node", ["scripts/vercel-output.mjs"], { env });
const out = run(vercel, ["deploy", "--prebuilt", ...(prod ? ["--prod"] : []), "--scope", SCOPE], { capture: true });

const urls = out.match(/https:\/\/[^\s]+/g) ?? [];
console.log(`\n${prod ? "PRODUCTION" : "PREVIEW"} deploy of ${project}: ${urls[urls.length - 1] ?? "(no URL in the CLI output)"}`);
if (prod) console.log("Live: https://project8.maxleiter.com  (rollback: vercel rollback <previous deployment id> --scope " + SCOPE + ")");
