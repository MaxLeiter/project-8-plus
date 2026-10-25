"use client";

import { Code, Menu, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Lockup } from "./Lockup";

/** A nav entry: `id` = a section on this page (#id), or `href` = a link elsewhere (another page). */
export type NavItem = { id?: string; href?: string; label: string; current?: boolean };

export function Header({ repo, nav, home }: { repo?: string | null; nav: NavItem[]; home: string }) {
  const NAV = nav;
  const [solid, setSolid] = useState(false);
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);

  useEffect(() => {
    const sections = NAV.filter((n) => n.id).map((n) => document.getElementById(n.id!)).filter(Boolean) as HTMLElement[];
    let raf = 0;
    const update = () => {
      raf = 0;
      setSolid(window.scrollY > 24);
      // current section = the last one whose top edge has passed 40 % of the viewport height
      const line = window.innerHeight * 0.4;
      let best: string | null = null;
      for (const s of sections) if (s.getBoundingClientRect().top <= line) best = s.id;
      setCurrent(best);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [NAV]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="site-header" data-solid={solid || open}>
      <div className="wrap bar">
        <a className="home" href={home} aria-label="Project 8 Plus home">
          <Lockup />
        </a>
        <nav className="nav" id="site-nav" aria-label="Sections" data-open={open}>
          {NAV.map((n) => (
            <a
              key={n.id ?? n.href}
              href={n.id ? `#${n.id}` : n.href}
              aria-current={n.current ? "page" : current === n.id && !!n.id}
              onClick={() => setOpen(false)}
            >
              {n.label}
            </a>
          ))}
        </nav>
        <div className="header-actions">
          {repo ? (
          <a className="btn btn-sm" href={repo} rel="noopener" aria-label="Project 8 Plus on GitHub">
            <Code size={16} aria-hidden />
            <span className="gh-label">GitHub</span>
          </a>
          ) : null}
          <button
            type="button"
            className="btn btn-sm menu-toggle"
            aria-expanded={open}
            aria-controls="site-nav"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X size={18} aria-hidden /> : <Menu size={18} aria-hidden />}
            <span className="sr-only">{open ? "Close the menu" : "Open the menu"}</span>
          </button>
        </div>
      </div>
    </header>
  );
}
