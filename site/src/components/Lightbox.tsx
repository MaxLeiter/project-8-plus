"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { LightboxItem } from "@/lib/media";

/**
 * One viewer for every image and clip on the page. Any element with data-lb (JSON, a LightboxItem) is part of the
 * gallery, in document order. A click on the media inside a .lb-frame, or on any [data-lb-open] button inside it,
 * opens the viewer there. Keyboard: Esc closes (native <dialog>), arrow keys step; touch: swipe left or right.
 * Pictures zoom: pinch (two fingers), double-tap or double-click toggles 2.5x at that point, + / - / 0 keys; one finger
 * or the mouse pans while zoomed; swipe steps only when not zoomed. Page zoom outside the viewer is never blocked.
 */
export function Lightbox() {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [items, setItems] = useState<LightboxItem[]>([]);
  const [index, setIndex] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const img = useRef<HTMLImageElement | null>(null);
  const z = useRef({ s: 1, x: 0, y: 0 });
  const ptrs = useRef(new Map<number, { x: number; y: number }>());
  const g = useRef<{ s: number; x: number; y: number; d: number; mx: number; my: number; px: number; py: number; moved: boolean; t: number } | null>(null);
  const lastTap = useRef({ t: 0, x: 0, y: 0 });
  const [zoomed, setZoomed] = useState(false);

  const apply = (anim = false) => {
    const el = img.current;
    if (!el) return;
    const { s, x, y } = z.current;
    el.style.transition = anim ? "transform 0.18s ease-out" : "none";
    el.style.transform = s === 1 ? "" : `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${s.toFixed(3)})`;
    setZoomed(s > 1);
  };
  const clampPan = () => {
    const el = img.current;
    if (!el) return;
    const { s } = z.current;
    const mx = (el.offsetWidth * (s - 1)) / 2;
    const my = (el.offsetHeight * (s - 1)) / 2;
    z.current.x = Math.max(-mx, Math.min(mx, z.current.x));
    z.current.y = Math.max(-my, Math.min(my, z.current.y));
  };
  /** Zoom to scale s keeping the screen point (px, py) over the same spot of the picture (base = the start state). */
  const zoomAt = (s: number, px: number, py: number, base = { ...z.current }) => {
    const el = img.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2 - z.current.x; // the untransformed centre
    const cy = r.top + r.height / 2 - z.current.y;
    const lx = (px - cx - base.x) / base.s;
    const ly = (py - cy - base.y) / base.s;
    z.current = s <= 1 ? { s: 1, x: 0, y: 0 } : { s, x: px - cx - lx * s, y: py - cy - ly * s };
    clampPan();
  };

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      const frame = t.closest<HTMLElement>(".lb-frame");
      if (!frame || t.closest(".clip-ctl, input, a, dialog")) return;
      const button = t.closest<HTMLElement>("[data-lb-open]");
      if (!button && !t.closest("img, video, picture")) return;
      e.preventDefault();
      const all = [...document.querySelectorAll<HTMLElement>("[data-lb]")];
      setItems(all.map((el) => JSON.parse(el.dataset.lb!) as LightboxItem));
      setIndex(Math.max(0, all.indexOf(frame)));
      setNarrow(window.innerWidth < 760);
      opener.current = button ?? frame.querySelector<HTMLElement>("[data-lb-open]");
      // pause inline clips while the viewer is open
      document.querySelectorAll<HTMLVideoElement>(".clip video").forEach((v) => v.pause());
      dialog.current?.showModal();
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  const step = useCallback((d: number) => setIndex((i) => (items.length ? (i + d + items.length) % items.length : 0)), [items.length]);
  // a new picture (or a closed viewer) starts unzoomed
  useEffect(() => {
    z.current = { s: 1, x: 0, y: 0 };
    ptrs.current.clear();
    g.current = null;
    setZoomed(false);
  }, [index, items]);
  const close = () => dialog.current?.close();
  const item = items[index];

  return (
    <dialog
      ref={dialog}
      className="lightbox"
      aria-label="Media viewer"
      onClose={() => {
        setItems([]);
        const el = opener.current;
        setTimeout(() => el?.focus({ preventScroll: true }), 0);
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") step(1);
        else if (e.key === "ArrowLeft") step(-1);
        else if (item?.type === "image" && ["+", "=", "-", "0"].includes(e.key) && img.current) {
          const r = img.current.getBoundingClientRect();
          const s = e.key === "0" ? 1 : e.key === "-" ? Math.max(1, z.current.s / 1.5) : Math.min(5, z.current.s * 1.5);
          zoomAt(s, r.left + r.width / 2, r.top + r.height / 2);
          apply(true);
        }
      }}
      onClick={(e) => {
        if (e.target === dialog.current) close();
      }}
    >
      {item ? (
        <>
          <div className="lb-bar">
            <span className="lb-count" aria-live="polite">
              {index + 1} / {items.length}
            </span>
            <button type="button" className="lb-icon" onClick={close} aria-label="Close the viewer">
              <X size={20} aria-hidden />
            </button>
          </div>
          <figure
            className={item.type === "image" ? (zoomed ? "lb-stage lb-zoom is-zoomed" : "lb-stage lb-zoom") : "lb-stage"}
            onPointerDown={(e) => {
              if (item.type !== "image") {
                swipe.current = { x: e.clientX, y: e.clientY };
                return;
              }
              if ((e.target as HTMLElement).closest("button")) return;
              try {
                e.currentTarget.setPointerCapture(e.pointerId);
              } catch {
                /* not an active pointer (synthetic events): fine without capture */
              }
              ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
              const ps = [...ptrs.current.values()];
              if (ps.length === 2) {
                const [p, q] = ps;
                g.current = { ...z.current, d: Math.hypot(p.x - q.x, p.y - q.y) || 1, mx: (p.x + q.x) / 2, my: (p.y + q.y) / 2, px: 0, py: 0, moved: true, t: 0 };
              } else if (ps.length === 1) {
                g.current = { ...z.current, d: 0, mx: 0, my: 0, px: e.clientX, py: e.clientY, moved: false, t: e.timeStamp };
              }
            }}
            onPointerMove={(e) => {
              if (item.type !== "image" || !ptrs.current.has(e.pointerId)) return;
              ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
              const st = g.current;
              if (!st) return;
              const ps = [...ptrs.current.values()];
              if (ps.length >= 2 && st.d) {
                const [p, q] = ps;
                const d = Math.hypot(p.x - q.x, p.y - q.y);
                const s = Math.max(1, Math.min(5, (st.s * d) / st.d));
                zoomAt(s, st.mx, st.my, { s: st.s, x: st.x, y: st.y });
                z.current.x += (p.x + q.x) / 2 - st.mx;
                z.current.y += (p.y + q.y) / 2 - st.my;
                clampPan();
                apply();
              } else if (ps.length === 1) {
                const dx = e.clientX - st.px;
                const dy = e.clientY - st.py;
                if (Math.abs(dx) + Math.abs(dy) > 8) st.moved = true;
                if (z.current.s > 1) {
                  z.current.x = st.x + dx;
                  z.current.y = st.y + dy;
                  clampPan();
                  apply();
                }
              }
            }}
            onPointerUp={(e) => {
              if (item.type !== "image") {
                const sw = swipe.current;
                swipe.current = null;
                if (!sw) return;
                const dx = e.clientX - sw.x;
                if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(e.clientY - sw.y) * 1.5) step(dx < 0 ? 1 : -1);
                return;
              }
              if (!ptrs.current.has(e.pointerId)) return;
              ptrs.current.delete(e.pointerId);
              const st = g.current;
              const left = [...ptrs.current.values()];
              if (left.length === 1) {
                // a pinch became a one-finger pan: pan on from here
                g.current = { ...z.current, d: 0, mx: 0, my: 0, px: left[0].x, py: left[0].y, moved: true, t: 0 };
                return;
              }
              g.current = null;
              if (!st || st.d) {
                if (z.current.s < 1.05) {
                  z.current = { s: 1, x: 0, y: 0 };
                  apply(true);
                }
                return;
              }
              const dx = e.clientX - st.px;
              const dy = e.clientY - st.py;
              if (!st.moved && e.timeStamp - st.t < 300) {
                const lt = lastTap.current;
                if (e.timeStamp - lt.t < 320 && Math.hypot(e.clientX - lt.x, e.clientY - lt.y) < 40) {
                  lastTap.current = { t: 0, x: 0, y: 0 };
                  if (z.current.s > 1) z.current = { s: 1, x: 0, y: 0 };
                  else zoomAt(2.5, e.clientX, e.clientY);
                  apply(true);
                } else lastTap.current = { t: e.timeStamp, x: e.clientX, y: e.clientY };
                return;
              }
              if (z.current.s === 1 && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
            }}
            onPointerCancel={(e) => {
              ptrs.current.delete(e.pointerId);
              g.current = null;
            }}
          >
            {item.type === "image" ? (
              <picture key={index}>
                {item.avif ? <source type="image/avif" srcSet={item.avif} sizes="100vw" /> : null}
                <source type={item.webp.includes(".jpg") ? "image/jpeg" : "image/webp"} srcSet={item.webp} sizes="100vw" />
                <img ref={img} src={item.src} alt={item.alt} width={item.w} height={item.h} decoding="async" draggable={false} />
              </picture>
            ) : (
              <video key={index} controls autoPlay muted loop playsInline preload="auto" poster={item.poster} width={item.w} height={item.h} aria-label={item.alt}>
                {(narrow ? item.mobile : item.sources).map((s) => (
                  <source key={s.src} src={s.src} type={s.type} />
                ))}
              </video>
            )}
            <figcaption className="lb-cap">{item.caption ?? item.alt}</figcaption>
          </figure>
          {items.length > 1 ? (
            <>
              <button type="button" className="lb-icon lb-prev" onClick={() => step(-1)} aria-label="Previous">
                <ChevronLeft size={26} aria-hidden />
              </button>
              <button type="button" className="lb-icon lb-next" onClick={() => step(1)} aria-label="Next">
                <ChevronRight size={26} aria-hidden />
              </button>
            </>
          ) : null}
        </>
      ) : null}
    </dialog>
  );
}
