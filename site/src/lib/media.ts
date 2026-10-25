import manifest from "@/generated/media.json";
import { media, notUploaded } from "./paths";

export type ImageFile = { src: string; w: number; type: string; bytes: number };
export type ImageEntry = { w: number; h: number; files: ImageFile[]; placeholder: string };
export type VideoEntry = {
  w: number;
  h: number;
  duration: number;
  audio: boolean;
  sources: { src: string; type: string; bytes: number }[];
  mobile?: { src: string; type: string; bytes: number }[] | null;
  poster: ImageEntry;
  draft?: string | null;
};

const images = manifest.images as unknown as Record<string, ImageEntry>;
const videos = manifest.videos as unknown as Record<string, VideoEntry>;

// Only with the opt-in Blob route (MEDIA_SOURCE=blob): fail the build early if any file is missing from
// media.manifest.json instead of shipping broken images. The default same-origin route needs no manifest.
if (process.env.MEDIA_SOURCE === "blob") {
  const all = [
    ...Object.values(images).flatMap((e) => e.files.map((f) => f.src)),
    ...Object.values(videos).flatMap((v) => [
      ...v.sources.map((s) => s.src),
      ...(v.mobile ?? []).map((s) => s.src),
      ...v.poster.files.map((f) => f.src),
    ]),
  ];
  const missing = notUploaded(all);
  if (missing.length) {
    throw new Error(
      `media.manifest.json has no uploaded URL for ${missing.length} file(s), e.g. ${missing.slice(0, 3).join(", ")}. ` +
        "Run node scripts/upload-media.mjs and commit the manifest (README: Deploying).",
    );
  }
}

export function image(id: string): ImageEntry {
  const e = images[id];
  if (!e) throw new Error(`media: no image "${id}" (run python scripts/media.py)`);
  return e;
}

export function video(id: string): VideoEntry {
  const e = videos[id];
  if (!e) throw new Error(`media: no video "${id}" (run python scripts/media.py)`);
  return e;
}

/** srcset for one format of an image entry. */
export function srcSet(entry: ImageEntry, type: string): string {
  return entry.files
    .filter((f) => f.type === type)
    .map((f) => `${media(f.src)} ${f.w}w`)
    .join(", ");
}

/** The fallback <img src>: the WebP (or JPEG) closest to `target` px wide. */
export function fallbackSrc(entry: ImageEntry, target = 1280): string {
  const pool = entry.files.filter((f) => f.type === "image/webp" || f.type === "image/jpeg");
  const best = pool.reduce((a, b) => (Math.abs(b.w - target) < Math.abs(a.w - target) ? b : a), pool[0]);
  return media(best.src);
}

/** What the client-side players need: resolved URLs (full and phone-sized), the poster and the intrinsic size. */
export function clipData(id: string) {
  const v = video(id);
  return {
    sources: v.sources.map((s) => ({ src: media(s.src), type: s.type })),
    mobile: (v.mobile ?? v.sources).map((s) => ({ src: media(s.src), type: s.type })),
    poster: fallbackSrc(v.poster, 1280),
    posterSmall: fallbackSrc(v.poster, 640),
    w: v.w,
    h: v.h,
  };
}

export type LightboxItem =
  | { type: "image"; alt: string; caption?: string; avif: string; webp: string; src: string; w: number; h: number }
  | { type: "video"; alt: string; caption?: string; sources: { src: string; type: string }[]; mobile: { src: string; type: string }[]; poster: string; w: number; h: number };

/** Lightbox data for an image: the full srcsets, so the dialog picks the size that fits the screen. */
export function lightboxImage(id: string, alt: string, caption?: string): LightboxItem {
  const e = image(id);
  const has = (t: string) => e.files.some((f) => f.type === t);
  return {
    type: "image",
    alt,
    caption,
    avif: has("image/avif") ? srcSet(e, "image/avif") : "",
    webp: has("image/webp") ? srcSet(e, "image/webp") : srcSet(e, "image/jpeg"),
    src: fallbackSrc(e, 1920),
    w: e.w,
    h: e.h,
  };
}

export function lightboxVideo(id: string, alt: string, caption?: string): LightboxItem {
  const c = clipData(id);
  return { type: "video", alt, caption, sources: c.sources, mobile: c.mobile, poster: c.poster, w: c.w, h: c.h };
}
