"use client";

import { List, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type TocGroup = { title: string; items: { href: string; label: string; count?: number }[] };
export type IndexEntry = { href: string; text: string; where: string; depth: number };

/** Open every closed <details> around `el` (a doc page, an answer) so a link into it lands on visible text. */
export function reveal(el: Element | null) {
  let p: Element | null = el;
  while (p) {
    if (p instanceof HTMLDetailsElement && !p.open) p.open = true;
    p = p.parentElement;
  }
}

export function go(href: string) {
  const id = decodeURIComponent(href.replace(/^#/, ""));
  const el = document.getElementById(id);
  if (!el) return;
  window.dispatchEvent(new CustomEvent("p8:reveal", { detail: id }));
  reveal(el);
  history.pushState(null, "", `#${id}`);
  requestAnimationFrame(() => {
    el.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    (el as HTMLElement).focus({ preventScroll: true });
  });
}

function score(text: string, terms: string[]): number {
  const t = text.toLowerCase();
  let s = 0;
  for (const term of terms) {
    const i = t.indexOf(term);
    if (i < 0) return -1;
    s += i === 0 ? 30 : /[\s._(:-]/.test(t[i - 1] ?? "") ? 18 : 6;
  }
  return s - t.length / 40;
}

function highlightTerms(text: string, terms: string[]) {
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

export function DocsSidebar({ groups, index }: { groups: TocGroup[]; index: IndexEntry[] }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [tocOpen, setTocOpen] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const terms = useMemo(() => q.toLowerCase().split(/\s+/).filter(Boolean), [q]);
  const results = useMemo(() => {
    if (!terms.length) return [];
    return index
      .map((e) => ({ e, s: Math.max(score(e.text, terms), score(`${e.where} ${e.text}`, terms) - 10) }))
      .filter((r) => r.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 40)
      .map((r) => r.e);
  }, [terms, index]);

  useEffect(() => setActive(0), [q]);

  // "/" focuses the search, like most docs sites
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && !t.isContentEditable) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // links from anywhere on the page (and the initial URL) open the doc that holds the target
  useEffect(() => {
    const open = (initial: boolean) => {
      const id = decodeURIComponent(location.hash.slice(1));
      if (!id) return;
      const el = document.getElementById(id);
      if (!el) return;
      window.dispatchEvent(new CustomEvent("p8:reveal", { detail: id }));
      const hidden = !!el.closest("details:not([open])") || (el instanceof HTMLDetailsElement && !el.open);
      reveal(el);
      if (el instanceof HTMLDetailsElement) el.open = true;
      // The browser jumps to the fragment before the page is hydrated, often while the target is still inside a
      // closed <details>; jump again once it is open and the layout has settled.
      if (hidden || initial) setTimeout(() => el.scrollIntoView({ block: "start", behavior: "instant" }), initial ? 120 : 0);
    };
    open(true);
    const onHash = () => open(false);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // highlight the TOC entry of the block being read
  useEffect(() => {
    const ids = groups.flatMap((g) => g.items.map((i) => i.href.slice(1)));
    const els = ids.map((id) => document.getElementById(id)).filter(Boolean) as HTMLElement[];
    let raf = 0;
    const update = () => {
      raf = 0;
      const line = 140;
      let best: string | null = null;
      for (const el of els) {
        const r = el.getBoundingClientRect();
        if (r.top <= line && r.bottom > line) best = el.id;
      }
      setCurrent(best);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => window.removeEventListener("scroll", onScroll);
  }, [groups]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((a) => Math.min(a + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((a) => Math.max(a - 1, 0));
      } else if (e.key === "Enter" && results[active]) {
        e.preventDefault();
        go(results[active].href);
      } else if (e.key === "Escape") {
        setQ("");
      }
    },
    [results, active],
  );

  return (
    <div className="docs-side" data-toc-open={tocOpen}>
      <div className="search" role="search">
        <Search size={16} aria-hidden className="lead" />
        <label htmlFor="docs-q" className="sr-only">
          Search the docs
        </label>
        <input
          ref={input}
          id="docs-q"
          type="search"
          placeholder="Search the docs"
          autoComplete="off"
          spellCheck={false}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
          aria-controls="docs-results"
          aria-activedescendant={results[active] ? `res-${active}` : undefined}
        />
        {!q ? <kbd aria-hidden>/</kbd> : null}
      </div>

      {q ? (
        results.length ? (
          <ul className="search-results" id="docs-results" role="listbox" aria-label={`${results.length} results`}>
            {results.map((r, i) => (
              <li key={r.href + i} role="option" id={`res-${i}`} aria-selected={i === active}>
                <a
                  href={r.href}
                  data-active={i === active}
                  onClick={(e) => {
                    e.preventDefault();
                    go(r.href);
                  }}
                >
                  {highlightTerms(r.text, terms)}
                  <small>{r.where}</small>
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="search-empty" id="docs-results" role="status">
            Nothing matches &ldquo;{q}&rdquo;. Try a function name like <code>hud.message</code>, or a word like
            &ldquo;rails&rdquo;.
          </p>
        )
      ) : (
        <>
          <button type="button" className="btn btn-sm toc-toggle" aria-expanded={tocOpen} onClick={() => setTocOpen((o) => !o)}>
            <List size={16} aria-hidden /> {tocOpen ? "Hide contents" : "Show contents"}
          </button>
          <nav className="toc-groups" aria-label="Docs contents">
            {groups.map((g) => (
              <div className="toc-group" key={g.title}>
                <h3>{g.title}</h3>
                <ul>
                  {g.items.map((it) => (
                    <li key={it.href}>
                      <a
                        href={it.href}
                        aria-current={current === it.href.slice(1)}
                        onClick={(e) => {
                          e.preventDefault();
                          setTocOpen(false);
                          go(it.href);
                        }}
                      >
                        <span>{it.label}</span>
                        {it.count ? <span className="count">{it.count}</span> : null}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </>
      )}
    </div>
  );
}
