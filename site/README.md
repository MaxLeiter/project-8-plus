# Project 8 Plus: the site

One page that is both the marketing site and the wiki: hero and trailer, what it is, the port, P8Plus, the
creator suite, "your first mod", the docs (install, requirements, troubleshooting, the modding guide, the Lua
API reference), FAQ, legal and credits.

- **Next.js (App Router), static export** (`output: "export"`): `npm run build` writes plain files to `out/`. No
  server, no API routes, no runtime on Vercel.
- **The docs are rendered from this repository at build time** (`src/lib/docs.ts` reads `../docs/modding/*.md`,
  `../docs/editor/README.md` and `../docs/modding/scripting_api.json`), so the site never drifts from the docs. Edit
  the docs, rebuild the site. Install, requirements and troubleshooting are written for the page
  (`src/components/sections/Docs.tsx`), from the native port's README, SETUP.md and KNOWN_ISSUES.md, which live in
  the other repository.
- **Design = the launcher's p8ui design system** (thp8-native `builder/p8ui/tokens.py`): the same colour tokens,
  Inter / Barlow Condensed / JetBrains Mono (subset to Latin by `scripts/fonts.py`) and Lucide icons.

**Branch `site-simple` (2026-10-18 draft):** the home page is the simple first-visitor page in Max's order: intro
(hero + download + trailer), HD textures (before/after slider), in-game shots, the mods that ship, the basics (own
the game, will it run, install in 3 steps), FAQ + known issues, legal footer. Every word on it is in
`src/content/simple.ts` (placeholder copy for Max; facts in `../FACTS.md`), components in
`src/components/simple/Sections.tsx`, styles in `src/app/simple.css`. The creator suite and "first mod" sections
moved to `/docs`; the old Port and Plus sections are no longer used (left in `src/components/sections/`). The HD pairs,
gallery and creator shots are the 2026-10-18 capture run (`../../thp8-native/docs/shots/site_20261018`, HD on/off
frozen-frame pairs).
Local preview: `npm run build`, then `python -m http.server <port> --bind 127.0.0.1 --directory out`.

## Working on it

```
cd site
npm install
python scripts/media.py      # once, and after a source clip or screenshot changes (needs ffmpeg + Pillow)
npm run dev                  # http://localhost:3000
npm run build                # static site in out/
```

`scripts/media.py` reads `media.config.json`: screenshots and recordings from the two repositories' working folders
(`../../thp8-native/docs/shots`, `../../thp8-mod/work/trailer/v2`; override with `P8_MEDIA_SHOTS` /
`P8_MEDIA_TRAILER`). It writes AVIF + WebP images at several widths, AV1 + H.264 MP4 clips with poster frames, into
`public/media/`, and the manifest `src/generated/media.json` (sizes, sources, a 24 px blurred placeholder per image).
It only re-encodes what changed; `--force` redoes everything, `--only <id>` one entry.

**The media is not in git.** It shows game content, and this repository keeps game-derived pixels out
(`.gitignore`, `publish/manifest.txt`). It is uploaded to Vercel Blob instead, and the committed
`media.manifest.json` maps each local file name to its blob URL. The build reads that manifest: a file with an
uploaded URL is linked there, anything else is served from `public/media` (that is what a local preview uses;
`MEDIA_SOURCE=local` ignores the manifest entirely). A build on Vercel stops with an error if any file the page uses
has no uploaded URL, so a deploy can never link to missing media.

`scripts/upload-media.mjs` does the upload, with the Vercel CLI (logged in with `vercel login`) or with vcup;
neither hands a token to the script. It uploads only the files the page uses (the ones listed in
`src/generated/media.json`), skips files whose SHA-256 matches the manifest, saves the manifest after every file
(so an interrupted run resumes), and retries failures on the next run. `--dry-run` lists what it would upload
without running anything; `--check` exits non-zero when something is missing or changed; `--help` lists every
option.

## Base path

The site works on a subdomain or under a path of another site:

| where | build with |
|---|---|
| `project8.maxleiter.com` (chosen: its own subdomain) | nothing (`BASE_PATH` unset, base path `/`) |
| `maxleiter.com/project8plus` (a path of the main site) | `BASE_PATH=/project8plus` |

`BASE_PATH` prefixes every page, script, font and media URL. `SITE_URL` (default `https://project8.maxleiter.com`) is the
public origin used for the social card; set it to the final address. `MEDIA_BASE_URL` (optional) is another origin for media files that are not in `media.manifest.json`.

For the subpath, the main `maxleiter.com` project forwards the path to this project with a rewrite in its own
`vercel.json` (or `next.config` `rewrites`):

```json
{ "rewrites": [
  { "source": "/project8plus", "destination": "https://<this-project>.vercel.app/project8plus/" },
  { "source": "/project8plus/:path*", "destination": "https://<this-project>.vercel.app/project8plus/:path*" }
] }
```

## Deploying

**Live** since 2026-10-07: Vercel project `project8plus` (team maxleiters-team), production domain
<https://project8.maxleiter.com> (maxleiter.com's DNS is Vercel's, so the subdomain verified by itself), also
<https://project8plus.vercel.app>. Media is served from the Blob store maxleiter-com-assets (`project8plus/` prefix).

**Deploying (self-contained, the default since 2026-10-20).** The media ships inside the project8plus deployment:
no Blob store, no tokens, no second project. From `site/`, with the Vercel CLI logged in (`vercel login`) and the
folder linked (`.vercel/project.json`, git-ignored; `vercel link --project project8plus --scope maxleiters-team`):

```
npm run deploy            # production: next build -> .vercel/output -> vercel deploy --prebuilt --prod; prints the URL
npm run deploy:preview    # the same without --prod (a preview URL)
```

`scripts/deploy.mjs` runs `npm run build` (same-origin `/media/...` links), then `scripts/vercel-output.mjs`, which
copies the export to `.vercel/output/static` and ships **only the media files the built pages reference** (it scans
every html/txt/js/css file for `/media/<file>`, srcset and posters included), renamed to `<name>.<sha256-8><ext>` with
every reference rewritten, so `/media/*` gets `Cache-Control: public, max-age=31536000, immutable` (routes in
`.vercel/output/config.json`; `/_next/static/*` too). Before deploying, `vercel ls project8plus --scope
maxleiters-team --prod` shows the current production deployment; `vercel rollback <id> --scope maxleiters-team`
goes back to it. Check the output locally with `python -m http.server <port> --directory .vercel/output/static`.

Use `scripts/vercel-output.mjs` rather than `vercel build`: the CLI's Next.js builder maps the exported pages to
extension-less paths and, with `trailingSlash`, redirected `/index` to `/index/`, so `/` served the 404 page.
Deploys are prebuilt because the build reads `../docs` and no Git repository is connected to the project.

**Optional: the Blob route** (media on Vercel Blob, the 2026-10-07 setup): upload (below), commit the manifest, then
`MEDIA_SOURCE=blob npm run build`, `node scripts/vercel-output.mjs --blob`, `vercel deploy --prebuilt --prod
--scope maxleiters-team`. With `MEDIA_SOURCE=blob` the build fails if a file has no uploaded URL.

### Uploading media

Needed whenever `python scripts/media.py` changed a file. Start with `node scripts/upload-media.mjs --dry-run`.

**With the Vercel CLI** (the default when a link dir is given). Blobs get fixed pathnames, `project8plus/<file>`
(`BLOB_PREFIX` changes the prefix), cached for a year; an existing blob is recorded, not overwritten, unless the
manifest says the file changed; every URL is checked with a HEAD request.

```
set VERCEL_LINK_DIR=<a folder linked (vercel link) to the project that owns the Blob store>
set BLOB_BASE_URL=https://tddeuevmbjbaaeoi.public.blob.vercel-storage.com
node scripts/upload-media.mjs                  # writes media.manifest.json
```

`vercel blob` takes the store's credentials from the linked folder (its `.env.local`, written by the CLI); delete
that file again after the upload. The script runs `%APPDATA%\npm\vercel.cmd` when it exists, else `vercel`
(`VERCEL_CMD` overrides).

**With vcup**: `npm i -g @maxleiter/vcup`, put `~/.vcuprc` (`{"url": ..., "token": ...}`) in place, then
`node scripts/upload-media.mjs --uploader vcup`. It runs `node ~/.vcup/cli.ts --raw` if that file exists, else
`vcup --raw` (`VCUP_CMD` overrides). vcup URLs carry a random suffix, which is why the manifest exists.

Then commit the manifest: `git add media.manifest.json && git commit -m "site: media manifest"`.

### Open items

- [ ] Real repository URLs in `src/content/site.ts` (`links.*`, `MaxLeiter/...` placeholders accepted for launch).
- [ ] Frame-rate numbers from the perf lead's clean "what ships" run in `perf` (`src/content/site.ts`); until then the
      page shows only the 30 fps original and the 120 fps target.
- [ ] Final trailer: point `trailer` in `media.config.json` at `P8Plus_launch_1080p60.mp4` (and drop `draft`), run
      `python scripts/media.py --only trailer`, upload, commit the manifest, redeploy.
- [ ] `python publish/scrub.py --scan site/out` (from the repository root, with the private term list) on the built
      site before each deploy: no e-mail addresses, user paths or personal names.

## Layout

```
site/
  media.config.json          media sources -> public/media (scripts/media.py)
  scripts/media.py           encoder (Pillow + ffmpeg)
  scripts/upload-media.mjs   uploads public/media (Vercel CLI or vcup), writes media.manifest.json (committed)
  scripts/vercel-output.mjs  out/ -> .vercel/output for vercel deploy --prebuilt
  scripts/shots.py           full-page screenshots of the built site (headless Chrome/Edge)
  scripts/fonts.py           subsets the p8ui fonts into src/fonts/
  src/app/                   layout (fonts, metadata), page, globals.css (all tokens and styles), icon.svg
  src/components/            Header, Hero media, Clip (in-view autoplay, pausable), BeforeAfter, Trailer dialog,
                             DocsSidebar (search over every heading + API entry), ApiReference
  src/components/sections/   one file per section of the page
  src/content/site.ts        links, the perf numbers, the site URL: every fact that is not in ../docs
  src/lib/docs.ts            markdown -> HTML (remark/rehype, Shiki with the p8ui code theme), link rewriting
  src/lib/media.ts, paths.ts the media manifest and the base path
```

Design rules (the user's, and p8ui's): no coloured side stripes on anything; one accent (pink) for actions,
selection and focus; selection is the wine fill; display type is Barlow Condensed caps; every status has an icon and a
word, never colour alone. Claims on the page are measured or come from the docs; nothing is promised that is not
shipped.

Accessibility: one `h1`, landmarks, a skip link, visible focus, every clip pausable and still with
`prefers-reduced-motion` (no autoplay, no smooth scrolling), alt text on every image, keyboard search (`/`) and a
keyboard-operable comparison slider. Phone width: 16 px gutters, no horizontal page scroll (wide tables scroll inside
their own box).
