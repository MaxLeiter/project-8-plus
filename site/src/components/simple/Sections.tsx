// The simple one-page site (branch site-simple). All visible words come from src/content/simple.ts.
import { ArrowRight, Check, Clock, Disc3, Download, HardDrive, MemoryStick, Monitor, Cpu, Gamepad2, TriangleAlert, Plus as PlusIcon } from "lucide-react";
import { HdSwitcher, type HdPair } from "./HdSwitcher";
import { HeroMedia } from "../HeroMedia";
import { Lockup } from "../Lockup";
import { MediaFrame } from "../MediaFrame";
import { Picture } from "../Picture";
import { Trailer } from "../Trailer";
import { links, reposPublic } from "@/content/site";
import * as copy from "@/content/simple";
import { clipData, fallbackSrc, image, lightboxImage, srcSet, video } from "@/lib/media";
import { asset } from "@/lib/paths";

function baImg(id: string, alt: string) {
  const e = image(id);
  return { src: fallbackSrc(e), srcSet: srcSet(e, "image/webp"), w: e.w, h: e.h, alt };
}

/** The download button: a link once the release is public, until then a disabled "coming soon" button. */
function DownloadButton({ size = 22, className = "" }: { size?: number; className?: string }) {
  if (reposPublic)
    return (
      <a className={`btn btn-play ${className}`} href={links.releases} rel="noopener">
        <Download size={size} aria-hidden strokeWidth={2.4} />
        {copy.hero.download}
      </a>
    );
  return (
    <button type="button" className={`btn btn-play s-soon-btn ${className}`} disabled aria-disabled="true">
      <Download size={size} aria-hidden strokeWidth={2.4} />
      {copy.hero.downloadSoon}
    </button>
  );
}

/** Big two-tone condensed title: first word white, second pink. */
function Title({ id, parts, as: Tag = "h2" }: { id: string; parts: string[]; as?: "h2" | "h3" }) {
  return (
    <Tag className="s-title" id={id}>
      {parts[0]} {parts[1] ? <span className="pink">{parts[1]}</span> : null}
    </Tag>
  );
}

export function DraftBanner() {
  return (
    <p className="s-draft" role="note">
      <TriangleAlert size={15} aria-hidden /> {copy.draftBanner}
    </p>
  );
}

export function Intro() {
  const trailer = video("trailer");
  const wide = video("hero-loop").poster;
  const tall = image("hero-m");
  return (
    <section className="hero s-hero" id="top" aria-labelledby="hero-title">
      <div className="hero-media">
        <picture>
          <source media="(max-width: 760px)" type="image/avif" srcSet={srcSet(tall, "image/avif")} sizes="100vw" />
          <source media="(max-width: 760px)" type="image/webp" srcSet={srcSet(tall, "image/webp")} sizes="100vw" />
          <source type="image/avif" srcSet={srcSet(wide, "image/avif")} sizes="100vw" />
          <img src={fallbackSrc(wide, 1280)} srcSet={srcSet(wide, "image/webp")} sizes="100vw" alt="" width={wide.w} height={wide.h} fetchPriority="high" decoding="async" />
        </picture>
      </div>
      <HeroMedia clip={clipData("hero-loop")} label="Crete skatepark and Houses, Project 8 Plus on PC" />
      <div className="hero-band">
        <div className="wrap">
          <h1 id="hero-title" className="hero-lockup-h">
            <Lockup className="hero-lockup" />
            <span className="sr-only">: Tony Hawk&rsquo;s Project 8 on PC</span>
          </h1>
        </div>
      </div>
      <div className="wrap s-hero-body">
        <p className="hero-kicker">{copy.hero.kicker}</p>
        <p className="s-hero-line">{copy.hero.line}</p>
        <div className="hero-cta">
          <DownloadButton className="s-download" />
          <Trailer clip={clipData("trailer")} draft={trailer.draft} label={copy.hero.trailer} />
        </div>
        <p className="s-hero-note">{copy.hero.downloadNote}</p>
      </div>
    </section>
  );
}

export function HdTextures() {
  const pairs: HdPair[] = copy.hd.pairs.map((q) => {
    const altOff = `${q.alt}, original textures`;
    const altOn = `${q.alt}, HD textures`;
    return {
      id: q.id,
      tab: q.tab,
      cap: q.cap,
      thumb: fallbackSrc(image(q.on), 640),
      before: baImg(q.off, altOff),
      after: baImg(q.on, altOn),
      lb: [lightboxImage(q.off, altOff), lightboxImage(q.on, altOn)],
    };
  });
  return (
    <section className="section s-section" id="hd" aria-labelledby="hd-title">
      <div className="wrap">
        <header className="s-head">
          <Title id="hd-title" parts={copy.hd.title} />
        </header>
        <HdSwitcher pairs={pairs} labels={copy.hd.labels} />
      </div>
    </section>
  );
}

export function Gallery() {
  const [first, ...rest] = copy.gallery.shots;
  return (
    <section className="section s-section" id="shots" aria-labelledby="shots-title">
      <div className="wrap">
        <header className="s-head">
          <Title id="shots-title" parts={copy.gallery.title} />
        </header>
      </div>
      <figure className="s-wide">
        <MediaFrame item={lightboxImage(first.id, first.alt)}>
          <Picture id={first.id} alt={first.alt} sizes="100vw" />
        </MediaFrame>
        <figcaption className="wrap caption">{first.cap}</figcaption>
      </figure>
      <div className="wrap">
        <div className="s-grid">
          {rest.map((s, i) => (
            <figure key={s.id} className={i === 0 ? "s-shot big" : "s-shot"}>
              <MediaFrame item={lightboxImage(s.id, s.alt)}>
                <Picture id={s.id} alt={s.alt} sizes={i === 0 ? "(max-width: 900px) 100vw, 66vw" : "(max-width: 900px) 100vw, 33vw"} />
              </MediaFrame>
              <figcaption className="caption">{s.cap}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Mods() {
  return (
    <section className="section s-section" id="mods" aria-labelledby="mods-title">
      <div className="wrap">
        <header className="s-head">
          <Title id="mods-title" parts={copy.mods.title} />
        </header>
        <div className="s-mods solo">
          <div>
            <h3 className="s-label">
              <Check size={16} aria-hidden strokeWidth={2.6} /> {copy.mods.onLabel}
            </h3>
            <ul className="s-modlist">
              {copy.mods.on.map((m) => (
                <li key={m.name}>
                  <b>{m.name}</b>
                  <span>{m.text}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="s-mods-note">
            <p className="s-small">{copy.mods.original}</p>
            <a className="link-arrow" href={asset("docs/#guide")}>
              {copy.mods.docsLink} <ArrowRight size={16} aria-hidden />
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

const RUN_ICONS = [Monitor, Cpu, MemoryStick, HardDrive, Gamepad2];

export function Basics() {
  return (
    <section className="section s-section" id="basics" aria-labelledby="basics-title">
      <div className="wrap">
        <header className="s-head">
          <Title id="basics-title" parts={["The", "basics"]} />
        </header>
        <div className="s-cards">
          <article className="s-card" id="own" aria-labelledby="own-title">
            <Disc3 size={28} aria-hidden className="s-card-icon" />
            <h3 id="own-title">{copy.own.title}</h3>
            {copy.own.body.map((p) => (
              <p key={p}>{p}</p>
            ))}
            <a className="link-arrow" href="https://rpcs3.net/quickstart" rel="noopener">
              {copy.own.howTo} <ArrowRight size={16} aria-hidden />
            </a>
          </article>

          <article className="s-card" id="run" aria-labelledby="run-title">
            <Monitor size={28} aria-hidden className="s-card-icon" />
            <h3 id="run-title">{copy.run.title}</h3>
            <dl className="s-spec">
              {copy.run.rows.map((r, i) => {
                const Icon = RUN_ICONS[i] ?? Monitor;
                return (
                  <div key={r.k}>
                    <dt>
                      <Icon size={15} aria-hidden /> {r.k}
                    </dt>
                    <dd>{r.v}</dd>
                  </div>
                );
              })}
            </dl>
            <div className="s-soon">
              <h4>
                <Clock size={15} aria-hidden /> {copy.comingSoon.title}
              </h4>
              <ul>
                {copy.comingSoon.items.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          </article>
        </div>

        <div className="s-install" id="install" aria-labelledby="install-title">
          <h3 className="s-subtitle" id="install-title">
            {copy.install.title}
          </h3>
          <ol className="s-steps">
            {copy.install.steps.map((s, i) => (
              <li key={s.t}>
                <span className="s-num" aria-hidden>
                  {i + 1}
                </span>
                <div>
                  <b>{s.t}</b>
                  <p>{s.d}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="s-install-cta">
            <DownloadButton size={20} />
            <a className="link-arrow" href={asset("docs/#install")}>
              Full install guide <ArrowRight size={16} aria-hidden />
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

export function FaqIssues() {
  // Known issues shrink as fixes ship; with none left the block and its heading are not rendered at all.
  const hasIssues = copy.issues.length > 0;
  return (
    <section className="section s-section" id="faq" aria-labelledby="faq-title">
      <div className={hasIssues ? "wrap s-faq-wrap" : "wrap s-faq-wrap solo"}>
        <div>
          <h2 className="s-subtitle" id="faq-title">
            FAQ
          </h2>
          <div className="s-faq">
            {copy.faq.map((f) => (
              <details key={f.q}>
                <summary>
                  {f.q}
                  <PlusIcon size={18} aria-hidden className="s-faq-icon" />
                </summary>
                <p>{f.a}</p>
              </details>
            ))}
          </div>
        </div>
        {hasIssues ? (
          <div id="known-issues">
            <h2 className="s-subtitle">Known issues</h2>
            <ul className="s-issues">
              {copy.issues.map((k) => (
                <li key={k.t}>
                  <TriangleAlert size={17} aria-hidden />
                  <div>
                    <b>{k.t}</b>
                    <p>{k.d}</p>
                  </div>
                </li>
              ))}
            </ul>
            {reposPublic ? (
              <p className="s-small">
                Something else? <a href={links.issues} rel="noopener">Report it</a> with the files from the install&rsquo;s
                logs folder.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function LegalFooter() {
  return (
    <footer className="site-footer s-footer" id="legal">
      <div className="footer-band">
        <div className="wrap">
          <Lockup />
          <p className="tag-line">
            Your game. <span>Your rules.</span>
          </p>
        </div>
      </div>
      <div className="wrap s-footer-body">
        <div className="s-legal">
          {copy.footer.legal.map((p) => (
            <p key={p}>{p}</p>
          ))}
        </div>
        <nav aria-label="Footer" className="s-footer-nav">
          {reposPublic ? (
            <>
              <a href={links.repo} rel="noopener">GitHub</a>
              <a href={links.issues} rel="noopener">Report a problem</a>
            </>
          ) : null}
          <a href={asset("docs/")}>Docs</a>
          <a href={asset("docs/#legal")}>{copy.footer.credits}</a>
        </nav>
      </div>
    </footer>
  );
}
