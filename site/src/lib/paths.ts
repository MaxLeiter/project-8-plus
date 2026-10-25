// Where the site lives and where its media comes from, decided at build time (see README.md):
//   BASE_PATH        "" for a subdomain, "/project8plus" (any path) when served under a path of another site
//   (default)             media is served from the same origin, /media/<file>; scripts/vercel-output.mjs ships only
//                         the files the pages reference inside the deployment (content-hashed names, long cache)
//   MEDIA_SOURCE=blob     opt-in: link the uploaded copies in media.manifest.json (Vercel Blob, scripts/upload-media.mjs)
//   MEDIA_BASE_URL        optional: another origin for the media
import uploaded from "../../media.manifest.json";

export const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
export const mediaBase = (process.env.NEXT_PUBLIC_MEDIA_BASE_URL || `${basePath}/media`).replace(/\/$/, "");

const manifest = (uploaded as { files: Record<string, { url: string }> }).files;
const useManifest = process.env.MEDIA_SOURCE === "blob";

/** URL of a file in public/ (fonts, icons), with the base path. */
export function asset(path: string): string {
  return `${basePath}/${path.replace(/^\//, "")}`;
}

/** URL of an encoded media file: its uploaded copy when the manifest has one, else public/media. */
export function media(file: string): string {
  return (useManifest && manifest[file]?.url) || `${mediaBase}/${file}`;
}

/** Files the manifest has no uploaded copy of (used to stop a production build that would link to nothing). */
export function notUploaded(files: string[]): string[] {
  return files.filter((f) => !manifest[f]?.url);
}
