"use client";

import { useRef, useState } from "react";
import { BeforeAfter } from "../BeforeAfter";
import type { LightboxItem } from "@/lib/media";

type Img = { src: string; srcSet: string; w: number; h: number; alt: string };
export type HdPair = { id: string; tab: string; cap: string; thumb: string; before: Img; after: Img; lb: [LightboxItem, LightboxItem] };

/**
 * Tabs under the before/after slider: one stock vs HD pair per view. WAI-ARIA tabs: arrow keys / Home / End move
 * between tabs, the slider keeps its own arrow keys. Only the chosen pair's images are in the page.
 */
export function HdSwitcher({ pairs, labels }: { pairs: HdPair[]; labels: [string, string] }) {
  const [i, setI] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const pick = (n: number) => {
    const k = (n + pairs.length) % pairs.length;
    setI(k);
    tabs.current[k]?.focus();
  };
  const p = pairs[i];
  return (
    <div className="s-hd">
      <figure className="s-ba" id="hd-panel" role="tabpanel" aria-labelledby={`hd-tab-${p.id}`}>
        <BeforeAfter key={p.id} name={`HD textures, ${p.tab}`} labels={labels} before={p.before} after={p.after} lb={p.lb} />
        <figcaption className="caption">{p.cap}</figcaption>
      </figure>
      <div className="s-tabs" role="tablist" aria-label="HD texture comparisons" style={{ "--n": pairs.length, "--n-phone": pairs.length <= 3 ? pairs.length : 2 } as React.CSSProperties}>
        {pairs.map((q, n) => (
          <button
            key={q.id}
            ref={(el) => {
              tabs.current[n] = el;
            }}
            type="button"
            role="tab"
            id={`hd-tab-${q.id}`}
            aria-selected={n === i}
            aria-controls="hd-panel"
            tabIndex={n === i ? 0 : -1}
            onClick={() => setI(n)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") pick(n + 1);
              else if (e.key === "ArrowLeft") pick(n - 1);
              else if (e.key === "Home") pick(0);
              else if (e.key === "End") pick(pairs.length - 1);
              else return;
              e.preventDefault();
            }}
          >
            <img src={q.thumb} alt="" width={160} height={90} loading="lazy" decoding="async" />
            <span>{q.tab}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
