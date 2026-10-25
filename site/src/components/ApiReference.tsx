"use client";

import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

export type Api = { name: string; kind: string; signature: string; doc: string; example?: string; ns: string };

import { apiId } from "@/lib/ids";

const COLLAPSED = 12;

/** The Lua API (docs/modding/scripting_api.json): every entry is in the HTML; filters only hide rows. */
export function ApiReference({ entries, namespaces }: { entries: Api[]; namespaces: { ns: string; count: number }[] }) {
  const [q, setQ] = useState("");
  const [ns, setNs] = useState<string | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    const onReveal = (e: Event) => {
      const id = (e as CustomEvent<string>).detail;
      if (id?.startsWith("api-")) {
        setQ("");
        setNs(null);
        setAll(true);
      }
    };
    window.addEventListener("p8:reveal", onReveal);
    if (location.hash.startsWith("#api-")) setAll(true);
    return () => window.removeEventListener("p8:reveal", onReveal);
  }, []);

  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const visible = useMemo(() => {
    return new Set(
      entries
        .filter((e) => (!ns || e.ns === ns) && terms.every((t) => `${e.signature} ${e.doc}`.toLowerCase().includes(t)))
        .map((e) => e.name),
    );
  }, [entries, ns, terms]);
  const filtering = !!ns || terms.length > 0;
  const collapsed = !filtering && !all;

  return (
    <div className="api">
      <div className="api-tools">
        <div className="search">
          <Search size={16} aria-hidden className="lead" />
          <label htmlFor="api-q" className="sr-only">
            Filter the Lua API
          </label>
          <input id="api-q" type="search" placeholder="Filter: name or words, e.g. teleport" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        <p className="caption" role="status" aria-live="polite">
          {filtering ? `${visible.size} of ${entries.length}` : `${entries.length} entries`}
        </p>
      </div>
      <div className="chips" role="group" aria-label="Show one table">
        <button type="button" className="chip" aria-pressed={ns === null} onClick={() => setNs(null)}>
          all <span className="c">{entries.length}</span>
        </button>
        {namespaces.map((n) => (
          <button type="button" key={n.ns} className="chip" aria-pressed={ns === n.ns} onClick={() => setNs(ns === n.ns ? null : n.ns)}>
            {n.ns} <span className="c">{n.count}</span>
          </button>
        ))}
      </div>
      <ul className="api-list" data-collapsed={collapsed}>
        {entries.map((e) => (
          <li className="api-item" id={apiId(e.name)} key={e.name} hidden={!visible.has(e.name)}>
            <div className="api-sig">
              <span className="kind">{e.kind}</span>
              <code>{e.signature}</code>
            </div>
            <p className="api-doc">{e.doc}</p>
            {e.example ? <pre className="api-ex">{e.example}</pre> : null}
          </li>
        ))}
      </ul>
      {collapsed ? (
        <div className="api-more">
          <button type="button" className="btn btn-sm" onClick={() => setAll(true)}>
            Show all {entries.length} entries
          </button>
        </div>
      ) : null}
    </div>
  );
}
