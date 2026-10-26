import { ArrowRight, Check, Maximize2 } from "lucide-react";
import { BeforeAfter } from "../BeforeAfter";
import { Picture } from "../Picture";
import { MediaFrame } from "../MediaFrame";
import { SectionHead } from "../SectionHead";
import { fallbackSrc, image, lightboxImage, srcSet } from "@/lib/media";

function baImg(id: string, alt: string) {
  const e = image(id);
  return { src: fallbackSrc(e), srcSet: srcSet(e, "image/webp"), w: e.w, h: e.h, alt };
}

export function Plus() {
  return (
    <section className="section" id="plus" aria-labelledby="plus-title">
      <div className="wrap">
        <SectionHead
          id="plus-title"
          title={
            <>
              What P8Plus <span className="pink">changes.</span>
            </>
          }
          lede={
            <>
              P8Plus is a plugin plus texture packs, and it leaves the gameplay alone. The launcher&rsquo;s{" "}
              <b>Play original</b> button starts the unmodified game whenever you want to compare.
            </>
          }
        />
      </div>

      <div className="wrap rows">
        <article className="row">
          <figure className="fig">
            <Shot id="g-900-side" alt="Tony Hawk spinning The 900 high above the Crete skatepark, the trick name and 9,000 points on screen" />
            <figcaption className="frame-cap">
              <span>
                <b>The 900</b>, Crete skatepark
              </span>
              <span>native port, P8Plus</span>
            </figcaption>
          </figure>
          <div className="row-copy">
            <h3>Higher frame rate</h3>
            <p>
              Menus and gameplay are no longer held to the PS3&rsquo;s frame rate. A few camera and skater blends in the
              engine step a fixed 1/60 of a second per update, which would speed them up when the game runs faster.
              P8Plus times them by the real frame time instead, so the game feels the same.
            </p>
          </div>
        </article>
      </div>

      <div className="wide-block">
        <div className="wrap">
          <div className="row-copy wide-copy">
            <h3>Widescreen and ultrawide</h3>
            <p>
              With Hor+ scaling, a wider screen shows more of the level at the sides instead of cutting off the top and
              bottom. 21:9 and 32:9 monitors are filled edge to edge. The HUD stays in a 16:9 frame in the middle,
              where it&rsquo;s easy to read.
            </p>
          </div>
        </div>
        <figure
          className="bleed-wide lb-frame"
          data-lb={JSON.stringify(lightboxImage("g-ultrawide", "The Houses level at 32:9, a skater in the cul-de-sac with the whole street, the pool and the houses in view"))}
        >
          <Picture id="g-ultrawide" alt="The Houses level at 32:9, a skater in the cul-de-sac with the whole street, the pool and the houses in view" sizes="100vw" />
          <div className="aspect-marks" aria-hidden>
            <div className="mark">
              <span>16:9</span>
            </div>
          </div>
          <figcaption className="sr-only">A 32:9 capture of Houses. The dashed frame marks what a 16:9 screen shows.</figcaption>
          <button type="button" className="lb-btn" data-lb-open aria-label="View larger: the 32:9 capture of Houses" title="View larger">
            <Maximize2 size={15} aria-hidden />
          </button>
        </figure>
        <div className="wrap">
          <p className="frame-cap">
            <span>
              <b>5120 &times; 1422</b>, Houses. The dashed frame is what a 16:9 screen shows.
            </span>
            <span>native port, P8Plus</span>
          </p>
        </div>
      </div>

      <div className="wrap rows">
        <article className="row flip">
          <figure className="fig">
            <Shot id="g-900-deck" alt="Tony Hawk grabbing a trick over the Crete bowl, with HD textures on the concrete, the graffiti and the deck" />
            <figcaption className="frame-cap">
              <span>
                <b>HD textures</b>, Crete skatepark
              </span>
              <span>native port, P8Plus</span>
            </figcaption>
          </figure>
          <div className="row-copy">
            <h3>HD textures</h3>
            <p>
              An AI upscaler rebuilds about 3,000 of the game&rsquo;s world textures at 2&times; or 4&times; resolution.
              It runs in the background once the install is done, so you can start playing right away. The sharper
              textures show up when it finishes. The port swaps them in as it draws, and the original files stay as
              they are.
            </p>
            <ul className="facts">
              <Fact>
                Model: <b>4xTextureDAT2_otf</b> by Philip Hofmann (CC BY 4.0)
              </Fact>
              <Fact>Most textures are upscaled 4&times;, noisy ones 2&times;, and some are left alone</Fact>
              <Fact>On by default. The launcher&rsquo;s Original look switch turns it off.</Fact>
            </ul>
          </div>
        </article>

        <article className="row">
          <figure className="fig">
            <Shot id="g-kickflip" alt="A skater mid-kickflip on a Houses street, smooth shoulders and elbows on the character model" />
            <figcaption className="frame-cap">
              <span>
                <b>Kickflip</b>, Houses
              </span>
              <span>native port, P8Plus</span>
            </figcaption>
          </figure>
          <div className="row-copy">
            <h3>Smoothed skaters</h3>
            <p>
              The pro skaters and the create-a-skater heads are smoothed, so shoulders, elbows and faces look rounder.
              They keep their original textures. The smoothed models are generated from your copy during the install,
              and <b>Original look</b> switches back to the stock ones.
            </p>
          </div>
        </article>

        <article className="row flip">
          <figure className="fig">
            <Shot id="ui-launcher-settings" alt="The launcher's Settings page: frame rate, display and widescreen, picture quality and effects, each with a plain description" />
            <figcaption className="frame-cap">
              <span>
                <b>Settings</b> in the launcher
              </span>
              <span>each option is described</span>
            </figcaption>
          </figure>
          <div className="row-copy">
            <h3>Graphics options</h3>
            <p>
              The launcher&rsquo;s Settings page covers render scale, FXAA, anisotropic filtering up to 16&times; and
              soft-edged shadow maps at 2&times; or 4&times;. Bloom, color grading, depth of field and draw distance
              can be tuned as well. <b>Reset to recommended</b> sets everything at once, and <b>PS3 look</b> puts it
              all back to stock.
            </p>
          </div>
        </article>

        <article className="row">
          <figure className="fig">
            <BeforeAfter
              name="Pause menu button prompts"
              labels={["PS3", "Xbox"]}
              before={baImg("xbox-pause-ps3", "The pause menu with PlayStation button prompts in the help bar")}
              after={baImg("xbox-pause-xbox", "The same pause menu with Xbox button prompts")}
              lb={[
                lightboxImage("xbox-pause-ps3", "The pause menu with PlayStation button prompts in the help bar"),
                lightboxImage("xbox-pause-xbox", "The same pause menu with Xbox button prompts"),
              ]}
            />
            <figcaption className="frame-cap">
              <span>
                <b>Drag</b> to compare the help bar
              </span>
              <span>Houses, pause menu</span>
            </figcaption>
          </figure>
          <div className="row-copy">
            <h3>Xbox buttons</h3>
            <p>
              Help bars, tips, menus and dialogs show A, B, X and Y instead of the PlayStation symbols. The new glyphs
              were drawn to match the game&rsquo;s art.
            </p>
            <a className="link-arrow doc-link" href="#create">
              The creator suite <ArrowRight size={16} aria-hidden />
            </a>
          </div>
        </article>
      </div>
    </section>
  );
}

function Fact({ children }: { children: React.ReactNode }) {
  return (
    <li>
      <Check size={16} aria-hidden strokeWidth={2.4} />
      <span>{children}</span>
    </li>
  );
}

function Shot({ id, alt, sizes }: { id: string; alt: string; sizes?: string }) {
  return (
    <MediaFrame item={lightboxImage(id, alt)}>
      <Picture id={id} alt={alt} sizes={sizes} />
    </MediaFrame>
  );
}
