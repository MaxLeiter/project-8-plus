import { ChevronDown, Download, FileText, Plus as PlusIcon, TriangleAlert } from "lucide-react";
import { ApiReference } from "../ApiReference";
import { DocsSidebar, type IndexEntry, type TocGroup } from "../DocsSidebar";
import { links, reposPublic } from "@/content/site";
import { loadApi, loadGuide, sourceUrl } from "@/lib/docs";
import { apiId } from "@/lib/ids";
import { asset } from "@/lib/paths";

type QA = { q: string; id: string; a: React.ReactNode };

const TROUBLE: QA[] = [
  {
    id: "t-rejected",
    q: "The builder rejects my game folder",
    a: (
      <>
        <p>
          Choose the folder that contains <code>PS3_GAME</code> and <code>PS3_DISC.SFB</code>, not{" "}
          <code>PS3_GAME</code> or <code>USRDIR</code>. The builder compares <code>EBOOT.BIN</code> and{" "}
          <code>PARAM.SFO</code> against BLUS30011 disc 01.02, the one supported release. Its message says whether the
          folder isn&rsquo;t a game, is a different game, or is Project 8 from another region or version.
        </p>
        <p>
          Other releases are refused because the port is translated from one specific executable. If you own one,{" "}
          <code>&quot;THP8 Launcher.cmd&quot; --identify &lt;folder&gt;</code> prints its title, version, size and
          SHA-1, which is all a release report needs. Don&rsquo;t send the files themselves.
        </p>
      </>
    ),
  },
  {
    id: "t-rpcs3",
    q: "It cannot find RPCS3, or decryption fails",
    a: (
      <p>
        Point the builder at <code>rpcs3.exe</code>. If decryption fails, make sure RPCS3 starts normally on its own.
        RPCS3&rsquo;s error message is in the builder&rsquo;s log in <code>logs\</code>.
      </p>
    ),
  },
  {
    id: "t-download",
    q: "The build stops while downloading the compiler",
    a: (
      <p>
        The first build downloads the zig compiler, about 97 MB, and checks its SHA-256. That step needs an internet
        connection. Run the builder again once you are online; it continues where it stopped. With HD textures on, the
        HD pack (about 44 MB) downloads after the build the same way and resumes on its own when the connection is back.
      </p>
    ),
  },
  {
    id: "t-verify",
    q: "The build stops at “verify”",
    a: (
      <p>
        The translated sources don&rsquo;t match the hashes published for this release, so the build wouldn&rsquo;t
        match what was released. Please report it with the log rather than working around it.
      </p>
    ),
  },
  {
    id: "t-compile",
    q: "The build stops while compiling",
    a: (
      <p>
        This is usually disk space or memory, since each parallel compile job needs about 1.2 GB. Free some of both and
        run the builder again. It continues from the last finished step. If it keeps failing, report it with the
        log.
      </p>
    ),
  },
  {
    id: "t-smartscreen",
    q: "“Windows protected your PC”, or my antivirus flagged it",
    a: (
      <>
        <p>
          The scripts and the executable aren&rsquo;t signed, so SmartScreen may warn. Choose <b>More info</b>, then{" "}
          <b>Run anyway</b>. Heuristic scanners also tend to flag freshly compiled, unsigned programs.
        </p>
        <p>
          Check before you dismiss a warning. Download only from the project&rsquo;s releases page and compare the
          archive with the published checksum. If your antivirus flags it, report the product and the detection
          name.
        </p>
      </>
    ),
  },
  {
    id: "t-slow",
    q: "The first start is slow",
    a: (
      <p>
        The game builds a data cache the first time it runs, as on a PS3. The build already does this in its last
        step, &ldquo;Preparing the game for first launch&rdquo;, so the first start is quick. After you change which
        mods are on, or play the original game, the cache is rebuilt: Play shows &ldquo;Preparing&rdquo; for a short
        while before the game starts.
      </p>
    ),
  },
  {
    id: "t-sound",
    q: "No sound, or the wrong device",
    a: (
      <p>
        Sound goes to the Windows default output and follows it when it changes. Pick another device on the
        launcher&rsquo;s Play page, or start the game with{" "}
        <code>play.cmd audio &quot;part of the device name&quot;</code>.
      </p>
    ),
  },
  {
    id: "t-pad",
    q: "My controller does nothing",
    a: (
      <p>
        The game reads XInput controllers, such as the Xbox pad. Other pads need a driver or mapper that makes them
        look like XInput, such as Steam Input or DS4Windows. Without a controller, use the keyboard: WASD steers,
        Space or Enter is Cross (ollie, accept), J is Square, L is Circle, I is Triangle, Esc pauses and the arrow
        keys are the D-pad. Every key can be changed in the launcher (Settings, Keyboard).
      </p>
    ),
  },
  {
    id: "t-fma",
    q: "The install does not start on another PC",
    a: (
      <p>
        On a CPU with AVX2 the builder compiles with FMA3, and the result only runs on CPUs that have it. Rather than
        copying an install to an older PC, build it there, or build a portable version with{" "}
        <code>--fma off</code>.
      </p>
    ),
  },
  {
    id: "t-logs",
    q: "Where are my saves and logs? What goes in a bug report?",
    a: (
      <>
        <p>
          Saves are in <code>hdd0\home\00000001\savedata</code> and logs are in <code>logs\</code>, both inside the
          install folder. Each game session writes a <code>play_*.log</code> next to <code>builder.log</code>.
        </p>
        <p>
          For a bug report, attach the logs and describe what you did. Never attach game files, decrypted files,{" "}
          <code>thp8.exe</code> or generated sources.
        </p>
      </>
    ),
  },
];

const REQUIREMENTS: [string, React.ReactNode][] = [
  ["Game", <>A folder dump of the US PS3 disc, BLUS30011 version 01.02, containing <code>PS3_GAME</code> and <code>PS3_DISC.SFB</code>. This is the layout RPCS3 uses. Other regions and versions, including the European BLES00014, aren&rsquo;t supported.</>],
  ["RPCS3", <>Installed. The builder runs <code>rpcs3 --decrypt</code> on <code>EBOOT.BIN</code> and two audio modules, with a temporary settings folder so your own setup isn&rsquo;t touched. No keys ship with the project.</>],
  ["Windows", <>Windows 10 or 11, 64-bit (x64). Not Windows on ARM.</>],
  ["Graphics", <>A GPU with Direct3D 12.</>],
  ["Processor", <>Any x64 CPU. On a CPU with AVX2 the build uses the faster FMA3 instructions, and that install then needs an AVX2 CPU. A fast single core helps most.</>],
  ["Memory", <>16 GB recommended. The build runs fewer parallel jobs with less.</>],
  ["Disk", <>About 4 GB free to install with the default add-ons, and about 2 GB once installed. Game data on the same NTFS drive is hard-linked. On another drive it&rsquo;s copied, which needs about 5 GB more.</>],
  ["Internet", <>Needed once, to download the zig compiler (about 97 MB) and, with HD textures on, the HD pack: the AI upscaler and its model, about 44 MB, no game content. Both are checked against pinned SHA-256s.</>],
  ["Build time", <>About 20 to 25 minutes on an 8-core CPU, once. An interrupted build resumes where it stopped.</>],
  ["HD textures", <>Made in the background after the install, about 1 hour on a recent graphics card and about 2 on a mid-range one, so you can play while it runs. They need a DirectX 12 graphics card; without one they are skipped.</>],
  ["Controller", <>An XInput pad such as an Xbox controller is recommended. The keyboard also works.</>],
  ["For mods", <>The editor, Texture Studio and Lua need nothing extra. For your own 3D models you&rsquo;ll want Blender 4.x or another glTF tool.</>],
];

export async function Docs() {
  const guide = await loadGuide();
  const api = loadApi();

  const groups: TocGroup[] = [
    {
      title: "Start here",
      items: [
        { href: "#install", label: "Install" },
        { href: "#requirements", label: "Requirements" },
        { href: "#troubleshooting", label: "Troubleshooting", count: TROUBLE.length },
      ],
    },
    {
      title: "Modding guide",
      items: guide.map((d) => ({ href: `#doc-${d.id}`, label: d.short, count: d.headings.length || undefined })),
    },
    { title: "Reference", items: [{ href: "#api", label: "Lua API", count: api.entries.length }] },
    { title: "Project", items: [{ href: "#what", label: "How it works" }, { href: "#legal", label: "Legal and credits" }] },
  ];

  const index: IndexEntry[] = [
    { href: "#install", text: "Install", where: "Start here", depth: 1 },
    { href: "#requirements", text: "Requirements", where: "Start here", depth: 1 },
    ...REQUIREMENTS.map(([k]) => ({ href: "#requirements", text: `Requirements: ${k}`, where: "Start here", depth: 2 })),
    ...TROUBLE.map((t) => ({ href: `#${t.id}`, text: t.q, where: "Troubleshooting", depth: 2 })),
    ...guide.flatMap((d) => [
      { href: `#doc-${d.id}`, text: d.title, where: `Modding guide: ${d.short}`, depth: 1 },
      ...d.headings.map((h) => ({ href: `#${h.id}`, text: h.text, where: d.short, depth: h.depth })),
    ]),
    ...api.entries.map((e) => ({ href: `#${apiId(e.name)}`, text: e.name, where: `Lua API: ${e.kind}`, depth: 2 })),
  ];

  return (
    <section className="docs-page wrap" id="docs" aria-labelledby="docs-title">
      <header className="docs-head">
        <h1 className="section-title" id="docs-title">
          The <span className="pink">docs.</span>
        </h1>
        <p className="lede">
          Start with the install steps if you&rsquo;re setting up. The modding guide and the Lua reference are generated
          from the project&rsquo;s documentation every time this site is built.
        </p>
      </header>
      <div className="docs-layout">
        <DocsSidebar groups={groups} index={index} />
        <div className="docs-main">
          <section className="docs-block" id="install" aria-labelledby="install-t">
            <header>
              <h2 id="install-t">Install</h2>
              <p>Have your disc dump ready. The install takes about 20 minutes, and most of that is waiting.</p>
            </header>
            <ol className="steps">
              <li>
                <div>
                  <p>
                    Download the launcher{" "}
                    {reposPublic ? (
                      <>
                        from the <a href={links.releases} rel="noopener">releases page</a>
                      </>
                    ) : (
                      <>(the download is coming soon)</>
                    )}{" "}
                    and unpack it anywhere you can write to. A short path without special characters works best.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    Run <code>THP8 Launcher.cmd</code>. It opens on the Setup page the first time. If SmartScreen warns
                    about an unsigned script, choose <b>More info</b>, then <b>Run anyway</b>.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    Choose the game folder that contains <code>PS3_GAME</code>. The launcher checks it before copying or
                    decrypting anything, and never writes to it.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    Check that RPCS3 was found, or point the launcher at <code>rpcs3.exe</code>. It&rsquo;s only used to
                    decrypt the game&rsquo;s executable.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    Choose an install folder and the add-ons you want. Project 8 Plus, the creator tools, smoothed
                    skaters, Xbox button prompts and HD textures are on by default.
                    Smoothed skaters are generated during the install, and HD textures in the background once it&rsquo;s
                    done. The original game is always built as well, and stays unchanged. Also included, but off
                    until you switch them on: the <b>dark boot screens</b> (a dark Project 8 Plus look for the loading
                    screens; a Setup option), <b>Create-a-Park</b> (build skate parks inside the game with the
                    controller; the Mods page) and <b>60 fps cutscenes</b> (experimental, check lip sync; Settings).
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    Press <b>Start building</b>. It takes about 20 to 25 minutes on an 8-core CPU and ends by preparing
                    the game for its first start.{" "}
                    <b>Low priority</b> keeps the PC usable while it runs. If the build stops, start it again and it
                    carries on from the last finished step.
                  </p>
                </div>
              </li>
              <li>
                <div>
                  <p>
                    When the build finishes, the launcher switches to the <b>Play</b> page. In the game,{" "}
                    <kbd>F11</kbd> toggles fullscreen. Closing the window quits, and a save that is in progress finishes
                    first.
                  </p>
                </div>
              </li>
            </ol>
            <div className="callout legal install-note">
              <Download size={18} aria-hidden />
              <p>
                The download only contains the builder. The translated code, the executable and the HD textures are
                made on your PC from the game, so they shouldn&rsquo;t be shared. Send people a link to this page
                instead.
              </p>
            </div>
          </section>

          <section className="docs-block" id="requirements" aria-labelledby="req-t">
            <header>
              <h2 id="req-t">Requirements</h2>
            </header>
            <div className="table-scroll" tabIndex={0} role="region" aria-label="Requirements">
              <table>
                <tbody>
                  {REQUIREMENTS.map(([k, v]) => (
                    <tr key={k}>
                      <th scope="row">{k}</th>
                      <td>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="callout warn">
              <TriangleAlert size={18} aria-hidden />
              <p>
                Online play, Linux, Steam Deck and Mac are coming later. Releases other than BLUS30011 disc 01.02 and
                Windows on ARM aren&rsquo;t supported.
              </p>
            </div>
          </section>

          <section className="docs-block" id="troubleshooting" aria-labelledby="trouble-t">
            <header>
              <h2 id="trouble-t">Troubleshooting</h2>
            </header>
            <div>
              {TROUBLE.map((t) => (
                <details className="qa" id={t.id} key={t.id}>
                  <summary>
                    {t.q}
                    <PlusIcon size={18} aria-hidden />
                  </summary>
                  <div className="a">{t.a}</div>
                </details>
              ))}
            </div>
          </section>

          <section className="docs-block" id="guide" aria-labelledby="guide-t">
            <header>
              <h2 id="guide-t">Modding guide</h2>
              <p>
                {guide.length} pages cover everything from a first mod to the game&rsquo;s file formats. Open one to
                read it here. Each heading has its own link.
              </p>
            </header>
            {guide.map((d) => (
              <details className="doc" id={`doc-${d.id}`} key={d.id}>
                <summary>
                  <h3>{d.short}</h3>
                  <p>{d.description}</p>
                  <span className="meta">
                    <span className="mins">{d.minutes} min read</span>
                    <span className="chev" aria-hidden>
                      <ChevronDown size={18} />
                    </span>
                  </span>
                </summary>
                <div className="doc-body">
                  <div className="doc-tools">
                    <span className="src">
                      <FileText size={14} aria-hidden className="inline-icon" /> <code className="mono">{d.source}</code>
                    </span>
                    {reposPublic && sourceUrl(d.source) ? (
                      <a href={sourceUrl(d.source)!} rel="noopener">
                        View source
                      </a>
                    ) : null}
                  </div>
                  <div className="prose" dangerouslySetInnerHTML={{ __html: d.html }} />
                </div>
              </details>
            ))}
          </section>

          <section className="docs-block" id="api" aria-labelledby="api-t">
            <header>
              <h2 id="api-t">Lua API reference</h2>
              <p>
                This list is generated from <code className="mono">docs/modding/scripting_api.json</code>, the same
                file behind the editor&rsquo;s autocomplete. It covers the script-mod host, {api.host}. The{" "}
                <a href="#doc-scripting">Lua script mods</a> page explains how the pieces fit together.
              </p>
            </header>
            <ApiReference entries={api.entries} namespaces={api.namespaces} />
          </section>
        </div>
      </div>
    </section>
  );
}
