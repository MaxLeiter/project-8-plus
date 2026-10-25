import { ArrowRight, Flag } from "lucide-react";
import { SectionHead } from "../SectionHead";
import { codeBlock, highlight } from "@/lib/docs";
import { asset } from "@/lib/paths";

const SPLITS = [
  {
    t: "1",
    title: "Open Create",
    text: "Open the Create page and pick a level. Reading a level from your game files takes about 10 seconds the first time and under a second after that.",
    keys: ["Ctrl", "5"],
  },
  {
    t: "2",
    title: "Place a ramp",
    text: "Pick Quarter pipe from the Create panel, turn it with the mouse wheel and click to drop it. Its collision and coping rail are already built. Use the gizmo to move, turn or scale it.",
    keys: ["W", "E", "R"],
  },
  {
    t: "3",
    title: "Draw a rail",
    text: "Pick Rail (drawn) and click points along a curb or a wall. Enter turns them into a grind rail on posts.",
    keys: ["Enter"],
  },
  {
    t: "4",
    title: "Add a goal",
    text: "In the Quests panel, add a collect goal and write its text. Its tapes go wherever the 3D view is looking.",
    keys: [],
  },
  {
    t: "5",
    title: "Test in game",
    text: "Test in game saves and builds the mod, then starts free skate at your camera position. Leave the game open and Live update reloads each edit as you finish it.",
    keys: ["F5"],
  },
  {
    t: "6",
    title: "Share it",
    text: "Share runs a real build to check the budgets, then writes a zip next to the project. A friend installs that zip from the Mods page, and it builds against their copy of the game.",
    keys: [],
    finish: true,
  },
];

export async function FiveMinutes() {
  // The real quick-start example from docs/modding/scripting.md: the first `lua` block of the page.
  const lua = codeBlock("docs/modding/scripting.md", "lua", 0);
  const luaHtml = await highlight(lua, "lua");
  return (
    <section className="section wrap" id="first-mod" aria-labelledby="first-mod-title">
      <SectionHead
        id="first-mod-title"
        title={
          <>
            Your <span className="pink">first mod.</span>
          </>
        }
        lede={
          <>
            This walkthrough builds a small park with one ramp, one rail and a collect goal, then zips it up for a
            friend. It all happens in the launcher, with no Blender and no command line.
          </>
        }
      />
      <div className="speedrun">
        <ol className="splits" aria-label="Six steps from an empty level to a shareable mod">
          {SPLITS.map((s) => (
            <li className={`split-row${s.finish ? " finish" : ""}`} key={s.t}>
              <span className="t">
                {s.finish ? <Flag size={14} aria-hidden className="inline-icon" /> : null}
                {s.t}
              </span>
              <div>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
                {s.keys.length ? (
                  <p className="keys" aria-label={`Keys: ${s.keys.join(", ")}`}>
                    {s.keys.map((k) => (
                      <kbd key={k}>{k}</kbd>
                    ))}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
        <aside className="code-card" aria-labelledby="lua-card-title">
          <header>
            <h3 id="lua-card-title">A mod in Lua</h3>
            <span className="tag neutral">mods\hello</span>
          </header>
          <pre className="tree" aria-label="Files of the mod">{`mods\\
  hello\\
    mod.json
    scripts\\main.lua`}</pre>
          <div dangerouslySetInnerHTML={{ __html: luaHtml }} />
          <footer>
            <p>
              Shows &ldquo;Hello, skater!&rdquo; when a level loads and keeps your best combo in the mod&rsquo;s save
              data. It&rsquo;s the quick-start example from the <a href={asset("docs/#doc-scripting")}>scripting guide</a>.
            </p>
            <p className="footer-links">
              <a className="link-arrow" href={asset("docs/#api")}>
                Lua API reference <ArrowRight size={15} aria-hidden />
              </a>
            </p>
          </footer>
        </aside>
      </div>
    </section>
  );
}
