"use client";

import { Play, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ClipData } from "./Clip";

/**
 * The PLAY button of the launcher, opening the trailer (with sound) in a modal dialog. Nothing of the trailer is
 * fetched until the dialog opens; phones get the 854 px encode.
 */
export function Trailer({ clip, draft, label = "Watch the trailer" }: { clip: ClipData; draft?: string | null; label?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [opened, setOpened] = useState(false);
  const [narrow, setNarrow] = useState(false);

  const open = () => {
    setNarrow(window.innerWidth < 760);
    setOpened(true);
    dialog.current?.showModal();
  };
  const close = () => dialog.current?.close();

  useEffect(() => {
    const v = video.current;
    if (opened && v) {
      v.currentTime = 0;
      v.play().catch(() => {});
    }
  }, [opened]);

  return (
    <>
      <button type="button" className="btn btn-play" onClick={open}>
        <Play size={22} aria-hidden strokeWidth={2.4} />
        {label}
      </button>
      <dialog
        ref={dialog}
        className="trailer"
        aria-labelledby="trailer-title"
        onClose={() => {
          video.current?.pause();
          setOpened(false);
        }}
        onClick={(e) => {
          if (e.target === dialog.current) close();
        }}
      >
        <header>
          <h2 id="trailer-title">
            Project 8 Plus trailer
            {draft ? <span className="tag warn draft">Rough cut</span> : null}
          </h2>
          <button type="button" className="btn btn-sm btn-ghost" onClick={close} aria-label="Close the trailer">
            <X size={18} aria-hidden /> Close
          </button>
        </header>
        {opened ? (
          <video ref={video} controls playsInline preload="auto" poster={clip.poster} width={clip.w} height={clip.h}>
            {(narrow ? clip.mobile : clip.sources).map((s) => (
              <source key={s.src} src={s.src} type={s.type} />
            ))}
          </video>
        ) : (
          <div className="trailer-placeholder" style={{ aspectRatio: `${clip.w} / ${clip.h}` }} />
        )}
        <footer>
          All gameplay was recorded in real time on the native PC port. The slow-motion replay is labelled, and
          live-edit waits are fast-forwarded with a note on screen. Music: &ldquo;Your Game, Your Rules&rdquo;, an
          original score for Project 8 Plus (CC BY 4.0).
        </footer>
      </dialog>
    </>
  );
}
