"use client";

import { ChevronsLeftRight, Maximize2 } from "lucide-react";
import type { LightboxItem } from "@/lib/media";
import { useState } from "react";

type Img = { src: string; srcSet: string; w: number; h: number; alt: string };

/** Drag (or use the arrow keys) to compare two frames with the same framing. */
export function BeforeAfter({ before, after, labels, name, lb }: { before: Img; after: Img; labels: [string, string]; name: string; lb?: [LightboxItem, LightboxItem] }) {
  const [pos, setPos] = useState(50);
  return (
    <>
    {lb ? <span hidden data-lb={JSON.stringify(lb[0])} /> : null}
    <div className={lb ? "ba lb-frame" : "ba"} style={{ "--pos": `${pos}%` } as React.CSSProperties} data-lb={lb ? JSON.stringify(lb[1]) : undefined}>
      <img src={before.src} srcSet={before.srcSet} sizes="(max-width: 900px) 100vw, 60vw" width={before.w} height={before.h} alt={before.alt} loading="lazy" decoding="async" />
      <img className="after" src={after.src} srcSet={after.srcSet} sizes="(max-width: 900px) 100vw, 60vw" width={after.w} height={after.h} alt={after.alt} loading="lazy" decoding="async" />
      <span className="ba-label l" aria-hidden>{labels[0]}</span>
      <span className="ba-label r" aria-hidden>{labels[1]}</span>
      <span className="divider" aria-hidden />
      <span className="knob" aria-hidden>
        <ChevronsLeftRight size={20} />
      </span>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={pos}
        onChange={(e) => setPos(Number(e.target.value))}
        aria-label={`${name}: drag to compare ${labels[0]} (left) and ${labels[1]} (right)`}
        aria-valuetext={`${100 - pos}% ${labels[0]}, ${pos}% ${labels[1]}`}
      />
      {lb ? (
        <button type="button" className="lb-btn" data-lb-open aria-label={`View larger: ${after.alt}`} title="View larger">
          <Maximize2 size={15} aria-hidden />
        </button>
      ) : null}
    </div>
    </>
  );
}
