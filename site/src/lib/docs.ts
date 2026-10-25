// Build-time rendering of the repository's own documentation (../docs), so the site never drifts from it.
// Runs only on the server during `next build`; nothing here reaches the browser except the produced HTML.
import fs from "node:fs";
import path from "node:path";
import GithubSlugger from "github-slugger";
import type { Element, ElementContent, Root as HastRoot } from "hast";
import { toString as hastToString } from "hast-util-to-string";
import rehypeShiki from "@shikijs/rehype";
import rehypeStringify from "rehype-stringify";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { unified } from "unified";
import { visit, SKIP } from "unist-util-visit";
import { p8Theme } from "./shiki-theme";
import { links, reposPublic } from "@/content/site";

export const REPO_ROOT = path.resolve(process.cwd(), "..");

export type DocHeading = { id: string; text: string; depth: number };
export type Doc = {
  id: string;
  title: string;
  short: string;
  description: string;
  source: string;
  html: string;
  headings: DocHeading[];
  minutes: number;
};

type DocDef = { id: string; file: string; short: string; description?: string };

// The modding guide, in reading order. `short` is the sidebar label. Descriptions default to the contents table
// of docs/modding/README.md, so they change when the docs do.
export const GUIDE: DocDef[] = [
  { id: "modding", file: "docs/modding/README.md", short: "The modding kit",
    description: "What a mod can do, the pages of the kit, and which operations are verified in game." },
  { id: "getting-started", file: "docs/modding/getting-started.md", short: "Getting started" },
  { id: "editor", file: "docs/editor/README.md", short: "The level editor",
    description: "The Create page: the 3D editor and creator hub, its tools and shortcuts, Test in game, live update and Share." },
  { id: "scripting", file: "docs/modding/scripting.md", short: "Lua script mods",
    description: "Script mods in Lua 5.4: mod.json, the sandbox, events, tasks, calling the game's scripts, examples and limits." },
  { id: "story", file: "docs/modding/story.md", short: "Stories" },
  { id: "goals", file: "docs/modding/goals.md", short: "Goals" },
  { id: "peds", file: "docs/modding/peds.md", short: "People" },
  { id: "operations", file: "docs/modding/operations.md", short: "Operation reference" },
  { id: "packaging", file: "docs/modding/packaging.md", short: "Mod packages" },
  { id: "collision-rails", file: "docs/modding/collision-rails.md", short: "Collision and rails" },
  { id: "blender", file: "docs/modding/blender.md", short: "Blender and glTF" },
  { id: "blender-addon", file: "docs/modding/blender-addon.md", short: "Blender add-on" },
  { id: "examples", file: "docs/modding/examples.md", short: "Example mods" },
  { id: "formats", file: "docs/modding/formats.md", short: "Game files" },
  { id: "limits", file: "docs/modding/limits.md", short: "Limits and budgets" },
];

const byFile = new Map(GUIDE.map((d) => [d.file, d]));

function read(rel: string): string {
  return fs.readFileSync(path.join(REPO_ROOT, rel), "utf8").replace(/\r\n/g, "\n");
}

/** Descriptions from the contents table of docs/modding/README.md: | [file.md](file.md) | text | */
function tableDescriptions(): Map<string, string> {
  const out = new Map<string, string>();
  const md = read("docs/modding/README.md");
  for (const m of md.matchAll(/^\|\s*\[([\w.-]+\.md)\]\([^)]*\)\s*\|\s*(.+?)\s*\|\s*$/gm)) {
    const text = m[2].replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/`/g, "");
    out.set(`docs/modding/${m[1]}`, text.charAt(0).toUpperCase() + text.slice(1) + ".");
  }
  return out;
}

function githubUrl(repoPath: string, isDir: boolean, frag = ""): string {
  return `${links.modRepo}/${isDir ? "tree" : "blob"}/${links.modBranch}/${repoPath}${frag ? "#" + frag : ""}`;
}

/** rehype plugin: drop the title, shift heading levels, prefix ids, rewrite links, wrap tables. */
function rehypeDocs(opts: { def: DocDef; headings: DocHeading[]; meta: { title: string } }) {
  const { def, headings, meta } = opts;
  const docDir = path.posix.dirname(def.file);
  return (tree: HastRoot) => {
    const slugger = new GithubSlugger();
    let h1s = 0;
    visit(tree, "element", (n: Element) => {
      if (n.tagName === "h1") h1s++;
    });
    const offset = h1s > 1 ? 3 : 2; // the first h1 is the title; docs with more h1s use them as parts
    let sawTitle = false;

    visit(tree, "element", (node: Element, index, parent) => {
      const m = /^h([1-6])$/.exec(node.tagName);
      if (m && parent && typeof index === "number") {
        const depth = Number(m[1]);
        const text = hastToString(node).trim();
        if (depth === 1 && !sawTitle) {
          sawTitle = true;
          meta.title = text;
          (parent.children as ElementContent[]).splice(index, 1);
          return [SKIP, index];
        }
        const slug = slugger.slug(text);
        const id = `${def.id}--${slug}`;
        node.tagName = `h${Math.min(6, depth + offset)}`;
        node.properties = { ...node.properties, id };
        node.children = [
          { type: "element", tagName: "a", properties: { href: `#${id}`, className: ["anchor"] }, children: node.children },
        ];
        headings.push({ id, text, depth });
        // links inside the heading are rewritten here, since the visitor skips the heading's children
        visit(node, "element", (a: Element) => {
          if (a.tagName === "a" && a.properties?.className?.toString() !== "anchor") {
            a.properties = { ...a.properties, href: rewriteHref(String(a.properties?.href ?? ""), def, docDir) };
            unlinkPrivate(a);
          }
        });
        return SKIP;
      }
      if (node.tagName === "a") {
        const href = String(node.properties?.href ?? "");
        node.properties = { ...node.properties, href: rewriteHref(href, def, docDir) };
        if (/^https?:/.test(String(node.properties.href))) node.properties.rel = ["noopener"];
        unlinkPrivate(node);
      }
    });

    // second pass: wrap tables so wide ones scroll inside their own box on a phone
    visit(tree, "element", (node: Element, index, parent) => {
      if (node.tagName === "table" && parent && typeof index === "number") {
        (parent.children as ElementContent[])[index] = {
          type: "element",
          tagName: "div",
          properties: { className: ["table-scroll"], tabIndex: 0, role: "region", ariaLabel: "Table" },
          children: [node],
        };
        return SKIP;
      }
    });
  };
}

/** While the repositories are private, a link into them (or a relative path outside this repo) becomes plain text. */
function unlinkPrivate(a: Element) {
  const href = String(a.properties?.href ?? "");
  const intoRepo = href.startsWith("https://github.com/MaxLeiter/");
  const unresolved = !!href && !/^(https?:|mailto:|#)/.test(href);
  if ((intoRepo && !reposPublic) || unresolved) {
    a.tagName = "span";
    a.properties = { className: ["repo-ref"] };
  }
}

function rewriteHref(href: string, def: DocDef, docDir: string): string {
  if (!href || /^(https?:|mailto:)/.test(href)) return href;
  if (href.startsWith("#")) return `#${def.id}--${href.slice(1)}`;
  const [p, frag = ""] = href.split("#");
  const repoPath = path.posix.normalize(path.posix.join(docDir, p));
  const target = byFile.get(repoPath);
  if (target) return frag ? `#${target.id}--${frag}` : `#doc-${target.id}`;
  if (repoPath.startsWith("..")) return href; // outside the repository: leave as written
  const isDir = !path.posix.extname(repoPath);
  return githubUrl(repoPath, isDir, frag);
}

let cache: Doc[] | null = null;

export async function loadGuide(): Promise<Doc[]> {
  if (cache) return cache;
  const descriptions = tableDescriptions();
  const docs: Doc[] = [];
  for (const def of GUIDE) {
    const md = read(def.file);
    const headings: DocHeading[] = [];
    const meta = { title: def.short };
    const file = await unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkRehype)
      .use(rehypeDocs, { def, headings, meta })
      .use(rehypeShiki, {
        theme: p8Theme,
        langs: ["lua", "json", "yaml"],
        defaultLanguage: "text",
        fallbackLanguage: "text",
        addLanguageClass: true,
      })
      .use(rehypeStringify)
      .process(md);
    const words = md.split(/\s+/).length;
    docs.push({
      id: def.id,
      title: meta.title,
      short: def.short,
      description: def.description ?? descriptions.get(def.file) ?? "",
      source: def.file,
      html: String(file),
      headings,
      minutes: Math.max(1, Math.round(words / 230)),
    });
  }
  cache = docs;
  return docs;
}

// ------------------------------------------------------------------------------------------------ Lua API
export type ApiEntry = { name: string; kind: string; signature: string; doc: string; example?: string; ns: string };

export function loadApi(): { host: string; entries: ApiEntry[]; namespaces: { ns: string; count: number }[] } {
  const j = JSON.parse(read("docs/modding/scripting_api.json")) as {
    host: string;
    entries: Omit<ApiEntry, "ns">[];
  };
  const entries = j.entries.map((e) => {
    const dot = e.name.indexOf(".");
    const ns = dot > 0 ? e.name.slice(0, dot) : e.kind === "table" ? e.name : "globals";
    return { ...e, ns };
  });
  const counts = new Map<string, number>();
  for (const e of entries) counts.set(e.ns, (counts.get(e.ns) ?? 0) + 1);
  const namespaces = [...counts.entries()].map(([ns, count]) => ({ ns, count }));
  return { host: j.host, entries, namespaces };
}

// ------------------------------------------------------------------------------------------------ snippets
/** The n-th fenced code block of a doc (0-based), for showing real code from the docs on the page. */
export function codeBlock(file: string, lang: string, n = 0): string {
  const md = read(file);
  const blocks = [...md.matchAll(new RegExp("^```" + lang + "\\n([\\s\\S]*?)^```", "gm"))].map((m) => m[1]);
  if (!blocks[n]) throw new Error(`no ${lang} block #${n} in ${file}`);
  return blocks[n].replace(/\n$/, "");
}

/** Highlight a snippet with the site theme (server only). */
export async function highlight(code: string, lang: string): Promise<string> {
  const { codeToHtml } = await import("shiki");
  return codeToHtml(code, { lang, theme: p8Theme });
}
