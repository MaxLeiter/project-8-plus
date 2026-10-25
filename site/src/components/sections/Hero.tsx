import { BookOpen, Wrench } from "lucide-react";
import { HeroMedia } from "../HeroMedia";
import { Lockup } from "../Lockup";
import { Trailer } from "../Trailer";
import { clipData, image, srcSet, video, fallbackSrc } from "@/lib/media";
import { asset } from "@/lib/paths";

export function Hero() {
  const trailer = video("trailer");
  const wide = video("hero-loop").poster;
  const tall = image("hero-m");
  return (
    <section className="hero" id="top" aria-labelledby="hero-title">
      <div className="hero-media">
        {/* the first paint: a portrait crop on phones, the video's first frame on wider screens */}
        <picture>
          <source media="(max-width: 760px)" type="image/avif" srcSet={srcSet(tall, "image/avif")} sizes="100vw" />
          <source media="(max-width: 760px)" type="image/webp" srcSet={srcSet(tall, "image/webp")} sizes="100vw" />
          <source type="image/avif" srcSet={srcSet(wide, "image/avif")} sizes="100vw" />
          <img
            src={fallbackSrc(wide, 1280)}
            srcSet={srcSet(wide, "image/webp")}
            sizes="100vw"
            alt=""
            width={wide.w}
            height={wide.h}
            fetchPriority="high"
            decoding="async"
          />
        </picture>
      </div>
      <HeroMedia clip={clipData("hero-loop")} label="Crete skatepark and Houses, native port with P8Plus" />
      <div className="hero-band">
        <div className="wrap">
          <h1 id="hero-title" className="hero-lockup-h">
            <Lockup className="hero-lockup" />
            <span className="sr-only">: Tony Hawk&rsquo;s Project 8, native on PC</span>
          </h1>
        </div>
      </div>
      <div className="wrap hero-body">
        <div>
          <p className="hero-kicker">Unofficial PC port</p>
          <p className="lede">
            Tony Hawk&rsquo;s Project 8, running natively on Windows. The launcher builds the game from a dump of your
            PS3 disc, so no game files are ever downloaded. P8Plus raises the frame rate and adds HD textures and
            ultrawide support. There&rsquo;s a level editor too, and it can change a park while
            you&rsquo;re skating in it.
          </p>
          <div className="hero-cta">
            <Trailer clip={clipData("trailer")} draft={trailer.draft} />
            <a className="btn" href={asset("docs/#install")}>
              <BookOpen size={18} aria-hidden /> Install guide
            </a>
            <a className="btn" href="#first-mod">
              <Wrench size={18} aria-hidden /> Make a mod
            </a>
          </div>
          <p className="hero-fine">
            A free fan project, not affiliated with Activision or Neversoft. It works with the US PS3 release,
            BLUS30011.
          </p>
        </div>
      </div>
    </section>
  );
}
