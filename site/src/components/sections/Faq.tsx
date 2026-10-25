import { Plus as PlusIcon } from "lucide-react";
import { SectionHead } from "../SectionHead";
import { links } from "@/content/site";
import { asset } from "@/lib/paths";

type QA = { id: string; q: string; a: React.ReactNode };

const FAQ: QA[] = [
  {
    id: "faq-emulator",
    q: "Is this an emulator?",
    a: (
      <p>
        No. An emulator interprets or recompiles the console&rsquo;s code while you play. Here the PPU and SPU code is
        translated to C/C++ once, before the game ever starts, and compiled into a Windows program. The{" "}
        <a href={links.ps3recomp} rel="noopener">ps3recomp</a> runtime replaces the PS3&rsquo;s system libraries with
        native code, and graphics go through Direct3D 12.
      </p>
    ),
  },
  {
    id: "faq-included",
    q: "Why isn't the game included? Why do I build it myself?",
    a: (
      <p>
        The game and its code belong to the publisher, and the translated code is derived from it. So the download
        only contains this project&rsquo;s own code and tools, and the game is built locally from a disc you
        own.
      </p>
    ),
  },
  {
    id: "faq-rpcs3",
    q: "Why do I need RPCS3?",
    a: (
      <p>
        The game&rsquo;s executable is encrypted on the disc. The builder asks your{" "}
        <a href={links.rpcs3} rel="noopener">RPCS3</a> install to decrypt it once, so this project doesn&rsquo;t ship
        keys or a decryption tool. RPCS3 never runs the game.
      </p>
    ),
  },
  {
    id: "faq-version",
    q: "I have the European or Japanese disc. Will it work?",
    a: (
      <p>
        No. The port is translated from one specific executable on the US disc, BLUS30011 version 01.02, and the
        builder refuses other releases. Each one would need its own translation. If you own a different release,{" "}
        <a href={asset("docs/#t-rejected")}>Troubleshooting</a> explains how to send its fingerprint.
      </p>
    ),
  },
  {
    id: "faq-share-build",
    q: "Can I share my built game or the HD textures?",
    a: (
      <p>
        No. The executable, the translated sources and the HD textures are derived from the game, and this project
        can&rsquo;t license them. Anyone who wants them needs their own copy, so send them here instead.
      </p>
    ),
  },
  {
    id: "faq-share-mods",
    q: "Can I share mods?",
    a: (
      <p>
        Yes. A mod made with the creator tools holds only your files and a list of operations. Anything it uses from
        the game is referenced by name and taken from each player&rsquo;s own copy at install time. Make the zip with{" "}
        <b>Share</b> in the editor or <b>Export</b> on the Mods page. Export warns you if a texture pack contains
        textures derived from the game.
      </p>
    ),
  },
  {
    id: "faq-free",
    q: "Is it free?",
    a: <p>Yes. It&rsquo;s a non-commercial fan project and doesn&rsquo;t take payment.</p>,
  },
  {
    id: "faq-original",
    q: "Does it change how the game plays?",
    a: (
      <p>
        P8Plus leaves the gameplay alone. <b>Play original</b> in the launcher starts the plain port, which runs at
        like the PS3 original. Mods can change whatever their authors build into them.
      </p>
    ),
  },
  {
    id: "faq-online",
    q: "Does online play work?",
    a: <p>No. Online play and other network features aren&rsquo;t supported.</p>,
  },
  {
    id: "faq-linux",
    q: "Does it run on Steam Deck, Linux or macOS?",
    a: <p>No. The port and the launcher are for Windows 10 and 11, x64 only.</p>,
  },
  {
    id: "faq-rpcs3-patch",
    q: "What happened to the RPCS3 patch?",
    a: (
      <p>
        Project 8 Plus started as a patch for RPCS3. That version is retired, though it&rsquo;s still in the
        repository history. New work only goes into the native port.
      </p>
    ),
  },
  {
    id: "faq-uninstall",
    q: "How do I uninstall it?",
    a: (
      <p>
        To keep your saves, copy <code>hdd0\home\00000001\savedata</code> somewhere first. Then delete the install
        folder, and the launcher folder too if you&rsquo;re done with it. The game folder is never written to, and
        hard-linked game data is only a second name for the same files, so your dump stays intact.
      </p>
    ),
  },
];

export function Faq() {
  const half = Math.ceil(FAQ.length / 2);
  return (
    <section className="section wrap" id="faq" aria-labelledby="faq-title">
      <SectionHead
        id="faq-title"
        title={
          <>
            Common <span className="pink">questions.</span>
          </>
        }
      />
      <div className="faq-cols">
        {[FAQ.slice(0, half), FAQ.slice(half)].map((col, i) => (
          <div key={i}>
            {col.map((f) => (
              <details className="qa" id={f.id} key={f.id}>
                <summary>
                  {f.q}
                  <PlusIcon size={18} aria-hidden />
                </summary>
                <div className="a">{f.a}</div>
              </details>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
