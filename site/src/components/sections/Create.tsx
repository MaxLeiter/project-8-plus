import { ArrowRight, Check } from "lucide-react";
import { MediaClip } from "../MediaClip";
import { Picture } from "../Picture";
import { MediaFrame } from "../MediaFrame";
import { SectionHead } from "../SectionHead";
import { loadApi } from "@/lib/docs";
import { lightboxImage, lightboxVideo, type LightboxItem } from "@/lib/media";
import { asset } from "@/lib/paths";

const TOOLS = [
  { id: "tool-launcher", label: "Launcher" },
  { id: "tool-textures", label: "Texture Studio" },
  { id: "tool-editor", label: "Level editor" },
  { id: "tool-stories", label: "Goals, people, stories" },
  { id: "tool-lua", label: "Lua" },
  { id: "tool-live", label: "Live link" },
];

export function Create() {
  const api = loadApi();
  return (
    <section className="section" id="create" aria-labelledby="create-title">
      <div className="wrap">
        <SectionHead
          id="create-title"
          title={
            <>
              The creator <span className="pink">suite.</span>
            </>
          }
          lede={
            <>
              Mods are made on the launcher&rsquo;s Create page. A mod holds its author&rsquo;s files and a list of
              operations, which the launcher applies to each player&rsquo;s copy of the game at install time. There
              are no game files inside, so a mod can be shared freely.
            </>
          }
        />
        <nav aria-label="The tools">
          <ul className="tool-index">
            {TOOLS.map((t) => (
              <li key={t.id}>
                <a href={`#${t.id}`}>
                  {t.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <div className="wrap rows create-rows">
        <Tool
          id="tool-launcher"
          title="Launcher and mod manager"
          media={
            <div className="tool-media">
              <Framed caption={<><b>Launcher tour</b></>} note="screen recording" lb={lightboxVideo("ui-launcher-tour", "A tour of the launcher's pages")}>
                <MediaClip id="ui-launcher-tour" label="A tour of the launcher's pages" />
              </Framed>
              <div className="pair">
                <Framed caption={<><b>Install from .zip</b></>} note="7 s" lb={lightboxVideo("ui-mods-install", "Installing a mod from a zip file on the Mods page")}>
                  <MediaClip id="ui-mods-install" label="Installing a mod from a zip file on the Mods page" />
                </Framed>
                <Framed caption={<><b>Mods</b> page</>} lb={lightboxImage("ui-launcher-mods", "The launcher's Mods page: installed packs with on and off switches, and the details of the selected pack")}>
                  <Picture id="ui-launcher-mods" alt="The launcher's Mods page: installed packs with on and off switches, and the details of the selected pack" sizes="(max-width: 900px) 100vw, 30vw" />
                </Framed>
              </div>
            </div>
          }
        >
          <p>
            Everything sits in one window with five pages, from Play to Create. On the Mods page you install a zip,
            set the load order and see which pack wins when two of them change the same file. An Xbox pad can drive
            the Play page.
          </p>
          <Facts
            items={[
              <>A zip is checked before anything is written. Executables, scripts such as .bat or .ps1, and paths outside the mods folder are refused.</>,
              <>A failed install leaves nothing behind</>,
              <>Export makes a byte-identical zip from the same pack</>,
            ]}
          />
        </Tool>

        <Tool
          id="tool-textures"
          title="Texture Studio"
          flip
          media={
            <Framed caption={<><b>Recolouring</b> a texture</>} note="screen recording" lb={lightboxVideo("ui-tex-recolour", "Recolouring a texture in the Texture Studio")}>
              <MediaClip id="ui-tex-recolour" label="Recolouring a texture in the Texture Studio" />
            </Framed>
          }
        >
          <p>
            All 7,805 of the game&rsquo;s textures can be searched and filtered here. Export any of them as a PNG, swap
            in your own picture or recolour it, then save the change into a pack that the game draws instead. The
            texture library is made locally from your game files.
          </p>
          <Facts items={[<>Replace at 1&times;, 2&times; or 4&times; the original size</>, <>Recolour by hue, saturation and brightness, with a live preview</>, <>Test in game from the same page</>]} />
        </Tool>

        <Tool
          id="tool-editor"
          title="Level editor"
          media={
            <div className="tool-media">
              <Framed caption={<><b>Placing a quarter pipe</b></>} note="screen recording" lb={lightboxVideo("ui-place-qp", "Placing and moving a quarter pipe in the level editor")}>
                <MediaClip id="ui-place-qp" label="Placing and moving a quarter pipe in the level editor" />
              </Framed>
              <div className="pair">
                <Framed caption={<><b>Drawing a rail</b></>} lb={lightboxVideo("ui-draw-rail", "Drawing a grind rail point by point")}>
                  <MediaClip id="ui-draw-rail" label="Drawing a grind rail point by point" />
                </Framed>
                <Framed caption={<><b>Park from scratch</b> on a 60 m slab</>} lb={lightboxVideo("ui-park-scratch", "Starting a new park from scratch")}>
                  <MediaClip id="ui-park-scratch" label="Starting a new park from scratch" />
                </Framed>
              </div>
            </div>
          }
        >
          <p>
            The editor renders the actual level, lightmaps and sky included. Its pieces are quarter pipes, kickers,
            funboxes, ledges, rails, banks, pool corners, stairs and boxes, and each one comes with collision and
            grindable copings. You can also draw a rail point by point along any surface. To start clean, hide the
            level and build on an empty slab.
          </p>
          <Facts
            items={[
              <>Import your own glTF or OBJ models, or pick the game&rsquo;s props from the asset browser</>,
              <>Prefabs for a mini ramp, a street line, a bowl and a stair set</>,
              <>Video memory and collision budgets update as you build, and Share refuses a mod that is over</>,
              <>
                <kbd>F5</kbd> starts free skate wherever the camera is looking
              </>,
            ]}
          />
          <a className="link-arrow doc-link" href={asset("docs/#doc-editor")}>
            Editor guide <ArrowRight size={16} aria-hidden />
          </a>
        </Tool>

        <Tool
          id="tool-stories"
          title="Goals and stories"
          flip
          media={
            <div className="tool-media">
              <Framed caption={<><b>Story panel</b></>} note="screen recording" lb={lightboxVideo("ui-story", "Editing a story's flow and dialogue in the Story panel")}>
                <MediaClip id="ui-story" label="Editing a story's flow and dialogue in the Story panel" />
              </Framed>
              <div className="pair">
                <Framed caption={<><b>The Kicker</b>, the example story, in game</>} lb={lightboxImage("g-rory", "In game: Rory's dialogue box says 'Whoa, careful, that's my kicker you almost ran over.'")}>
                  <Picture id="g-rory" alt="In game: Rory's dialogue box says 'Whoa, careful, that's my kicker you almost ran over.'" sizes="(max-width: 900px) 100vw, 30vw" />
                </Framed>
                <Framed caption={<><b>People</b> placed in Houses</>} lb={lightboxImage("g-people", "Two pedestrians placed by a mod stand on a Houses sidewalk, one waving")}>
                  <Picture id="g-people" alt="Two pedestrians placed by a mod stand on a Houses sidewalk, one waving" sizes="(max-width: 900px) 100vw, 30vw" />
                </Framed>
              </div>
            </div>
          }
        >
          <p>
            The six goal types are score, combo, collect, race, gap and trick. A goal can start from a marker,
            from a person you talk to, from the pause menu or from a script. People can stand around, walk a path or
            skate a line. Stories tie it together with chapters, dialogue choices, cutscenes and saved progress.
          </p>
          <Facts
            items={[
              <>The story flow and each conversation are edited as node graphs</>,
              <>Scrubbing the cutscene timeline moves the editor camera</>,
              <>
                <b>Play from here</b> jumps the running game to any chapter or quest
              </>,
            ]}
          />
          <a className="link-arrow doc-link" href={asset("docs/#doc-story")}>
            Story format <ArrowRight size={16} aria-hidden />
          </a>
        </Tool>

        <Tool
          id="tool-lua"
          title="Lua mods"
          media={
            <Framed caption={<><b>Scripts panel</b></>} note="screen recording" lb={lightboxVideo("ui-lua", "Writing a Lua script mod in the editor's Scripts panel")}>
              <MediaClip id="ui-lua" label="Writing a Lua script mod in the editor's Scripts panel" />
            </Framed>
          }
        >
          <p>
            Script mods are written in Lua 5.4. They react to events such as a landed trick, a gap or a finished goal,
            and they call into the game&rsquo;s own script system to open dialogs, change the HUD or move the skater.
          </p>
          <Facts
            items={[
              <>
                {api.entries.length} documented API entries, from <code className="mono">qb.call</code> to{" "}
                <code className="mono">skater.teleport</code>
              </>,
              <>Each mod gets its own sandboxed Lua state and its own save data</>,
              <>Script errors are logged with the file and line, and the game keeps running</>,
            ]}
          />
          <a className="link-arrow doc-link" href={asset("docs/#api")}>
            Lua API reference <ArrowRight size={16} aria-hidden />
          </a>
        </Tool>

        <Tool
          id="tool-live"
          title="Live editing"
          flip
          media={
            <div className="tool-media">
              <Framed caption={<><b>Live update</b>, editor side</>} note="screen recording" lb={lightboxVideo("ui-live-edit", "Moving a ramp in the editor while the game is running")}>
                <MediaClip id="ui-live-edit" label="Moving a ramp in the editor while the game is running" />
              </Framed>
              <Framed caption={<><b>Editor overlay</b> in the running game</>} lb={lightboxImage("livelink-overlay", "In game, seen from the free camera: grind rails outlined in pink and a selection gizmo drawn over a rooftop")}>
                <Picture id="livelink-overlay" alt="In game, seen from the free camera: grind rails outlined in pink and a selection gizmo drawn over a rooftop" />
              </Framed>
            </div>
          }
        >
          <p>
            The editor stays connected to the running game. Moving a ramp reloads only that part of the level, which
            takes three or four seconds. Rail and goal changes need a full level reload of about 7.5 seconds, and the
            game never restarts. You can also teleport the skater, fly the game camera or start a goal from the editor,
            and script errors show up in its console.
          </p>
          <Facts items={[<>The link only listens on this PC and needs a per-session token</>, <>Lua mods reload in place without reloading the level</>]} />
        </Tool>
      </div>
    </section>
  );
}

function Tool({
  id,
  title,
  media,
  flip,
  children,
}: {
  id: string;
  title: string;
  media: React.ReactNode;
  flip?: boolean;
  children: React.ReactNode;
}) {
  return (
    <article className={`row tool${flip ? " flip" : ""}`} id={id} aria-labelledby={`${id}-t`}>
      <div className="fig">{media}</div>
      <div className="row-copy">
        <h3 id={`${id}-t`}>{title}</h3>
        {children}
      </div>
    </article>
  );
}

function Framed({ children, caption, note, lb }: { children: React.ReactNode; caption: React.ReactNode; note?: string; lb?: LightboxItem }) {
  return (
    <figure className="fig">
      {lb ? <MediaFrame item={lb}>{children}</MediaFrame> : <div className="frame">{children}</div>}
      <figcaption className="frame-cap">
        <span>{caption}</span>
        {note ? <span>{note}</span> : null}
      </figcaption>
    </figure>
  );
}

function Facts({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="facts">
      {items.map((it, i) => (
        <li key={i}>
          <Check size={16} aria-hidden strokeWidth={2.4} />
          <span>{it}</span>
        </li>
      ))}
    </ul>
  );
}
