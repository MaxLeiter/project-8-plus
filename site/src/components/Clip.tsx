"use client";

import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export type ClipData = {
  sources: { src: string; type: string }[];
  mobile: { src: string; type: string }[];
  poster: string;
  posterSmall: string;
  w: number;
  h: number;
};

/**
 * Inline autoplay only where it is cheap and expected: a wide screen with a mouse, no data saver, no slow
 * connection, no reduced-motion preference. Everywhere else a clip is a still with a play button that opens it in
 * the lightbox.
 */
export function canAutoplay(): boolean {
  if (typeof window === "undefined") return false;
  const nav = navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } };
  if (nav.connection?.saveData) return false;
  if (/(^|-)2g|3g/.test(nav.connection?.effectiveType ?? "")) return false;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  return window.matchMedia("(min-width: 900px) and (hover: hover) and (pointer: fine)").matches;
}

/**
 * A muted, looping screen recording. Nothing but a lazy poster image is fetched until the clip is on screen (and
 * then only where canAutoplay() allows); the phone-sized encode is used when the clip is drawn narrow.
 */
export function Clip({ clip, label, className }: { clip: ClipData; label: string; className?: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [auto, setAuto] = useState(false);
  const [sources, setSources] = useState<{ src: string; type: string }[] | null>(null);
  const [playing, setPlaying] = useState(false);
  const [shown, setShown] = useState(false);
  const userPaused = useRef(false);

  useEffect(() => {
    const v = ref.current;
    if (!v || !canAutoplay()) return;
    setAuto(true);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onFrame = () => setShown(true);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("playing", onFrame);
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting && e.intersectionRatio >= 0.35 && !userPaused.current) {
          setSources((cur) => cur ?? (v.getBoundingClientRect().width < 560 ? clip.mobile : clip.sources));
          requestAnimationFrame(() => v.play().catch(() => {}));
        } else if (!e.isIntersecting || e.intersectionRatio < 0.2) {
          v.pause();
        }
      },
      { threshold: [0, 0.2, 0.35, 0.6] },
    );
    io.observe(v);
    return () => {
      io.disconnect();
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("playing", onFrame);
    };
  }, [clip]);

  // sources change -> the element must reload them
  useEffect(() => {
    if (sources && ref.current) {
      ref.current.load();
      if (!userPaused.current) ref.current.play().catch(() => {});
    }
  }, [sources]);

  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) {
      userPaused.current = false;
      v.play().catch(() => {});
    } else {
      userPaused.current = true;
      v.pause();
    }
  };

  return (
    <div className={["clip", className].filter(Boolean).join(" ")}>
      <img
        className="clip-poster"
        src={clip.posterSmall}
        srcSet={`${clip.posterSmall} 640w, ${clip.poster} 1280w`}
        sizes="(max-width: 900px) 100vw, 50vw"
        width={clip.w}
        height={clip.h}
        alt=""
        loading="lazy"
        decoding="async"
      />
      <video
        ref={ref}
        muted
        loop
        playsInline
        preload="none"
        width={clip.w}
        height={clip.h}
        aria-label={label}
        disablePictureInPicture
        data-shown={shown}
      >
        {sources?.map((s) => <source key={s.src} src={s.src} type={s.type} />)}
      </video>
      {auto ? (
        <div className="clip-ctl">
          <button
            type="button"
            className="media-toggle"
            onClick={toggle}
            aria-label={playing ? `Pause: ${label}` : `Play: ${label}`}
            title={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause size={15} aria-hidden /> : <Play size={15} aria-hidden />}
          </button>
        </div>
      ) : (
        <button type="button" className="clip-big-play" data-lb-open aria-label={`Play: ${label}`}>
          <span>
            <Play size={18} aria-hidden fill="currentColor" /> Play
          </span>
        </button>
      )}
    </div>
  );
}
