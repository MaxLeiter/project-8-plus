"use client";

import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { canAutoplay, type ClipData } from "./Clip";

/**
 * The hero's background footage, on top of the poster picture that the server renders. The video is fetched only
 * on a wide screen without data saver or reduced motion, and only after the page has finished loading, so it never
 * competes with the first paint. Always pausable.
 */
export function HeroMedia({ clip, label }: { clip: ClipData; label: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [active, setActive] = useState(false);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!canAutoplay()) return;
    let cancelled = false;
    const start = () => {
      const idle = (window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
      const go = () => !cancelled && setActive(true);
      if (idle) idle(go, { timeout: 2000 });
      else setTimeout(go, 400);
    };
    if (document.readyState === "complete") start();
    else window.addEventListener("load", start, { once: true });
    return () => {
      cancelled = true;
      window.removeEventListener("load", start);
    };
  }, []);

  useEffect(() => {
    const v = ref.current;
    if (!active || !v) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    v.addEventListener("playing", onPlay);
    v.addEventListener("pause", onPause);
    v.play().catch(() => {});
    return () => {
      v.removeEventListener("playing", onPlay);
      v.removeEventListener("pause", onPause);
    };
  }, [active]);

  if (!active) return null;
  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  };
  return (
    <>
      <video
        ref={ref}
        className="hero-video"
        data-playing={playing}
        muted
        loop
        playsInline
        preload="auto"
        width={clip.w}
        height={clip.h}
        aria-hidden
        tabIndex={-1}
        disablePictureInPicture
      >
        {clip.sources.map((s) => (
          <source key={s.src} src={s.src} type={s.type} />
        ))}
      </video>
      <button
        type="button"
        className="media-toggle hero-toggle"
        onClick={toggle}
        aria-label={playing ? `Pause the background video (${label})` : `Play the background video (${label})`}
      >
        {playing ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}
        {playing ? "Pause footage" : "Play footage"}
      </button>
    </>
  );
}
