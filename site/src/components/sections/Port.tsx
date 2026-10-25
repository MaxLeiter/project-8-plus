import { CircleSlash, Gamepad2, Info, Maximize, MonitorSmartphone, Save, Volume2, Zap } from "lucide-react";
import { SectionHead } from "../SectionHead";
import { links } from "@/content/site";
import { asset } from "@/lib/paths";

const MODERN = [
  { icon: Maximize, title: "Window and fullscreen", text: "The game opens in a resizable window sized to your screen. F11 toggles borderless fullscreen, and the launcher can set an exact window size." },
  { icon: Zap, title: "Shaders built at install", text: "Every shader and graphics pipeline is compiled for your GPU during the install, so the game doesn't stall to compile them in a level." },
  { icon: MonitorSmartphone, title: "High refresh rates", text: "The game clock no longer depends on the PS3's 60 Hz signal, so frames can be paced for a high-refresh screen or left uncapped on a VRR display." },
  { icon: Gamepad2, title: "Xbox controllers", text: "Any XInput pad works, analog triggers included. With no pad connected, the keyboard is mapped instead." },
  { icon: Volume2, title: "Sound output", text: "Sound goes to the Windows default device and follows it when you switch. You can also pick a device on the launcher's Play page." },
  { icon: Save, title: "Saves", text: "Saves are stored in the install folder. Closing the window asks the game to save and quit, like the PS3's own quit request." },
];

const STATUS = [
  {
    tone: "info",
    icon: Info,
    title: "Known limitations",
    items: [
      "The first start is slower because the game builds its data cache. Changing which mods are active rebuilds it.",
      "High-frame-rate cutscenes are experimental and off by default.",
      "Pads that aren't XInput need a driver or mapper that makes them look like one.",
      "On a CPU with AVX2 the build uses FMA3, and that install won't start on an older CPU. Build with --fma off for one that runs anywhere.",
      "The launcher scripts and the built executable are unsigned. SmartScreen may warn about them, and some antivirus products flag them.",
    ],
  },
  {
    tone: "no",
    icon: CircleSlash,
    title: "Not supported",
    items: [
      "Online play and other network features",
      "Releases other than BLUS30011 disc 01.02 (Europe, Japan)",
      "Linux, macOS, Windows on ARM",
      "Running without your own copy of the game",
    ],
  },
];

export function Port() {
  return (
    <section className="section wrap" id="port" aria-labelledby="port-title">
      <SectionHead
        id="port-title"
        title={
          <>
            Compiled for <span className="pink">Windows.</span>
          </>
        }
        lede={
          <>
            The game&rsquo;s PowerPC and Cell SPU code is translated to C/C++ ahead of time by{" "}
            <a href={links.ps3recomp} rel="noopener">ps3recomp</a>, then compiled into an ordinary Windows executable.
            The PS3 system libraries it calls are rewritten as native code, and graphics run on Direct3D 12. There is
            no emulator underneath.
          </>
        }
      />

      <div className="pc-req" id="recommended-pc">
        <h3 className="h-cond sub-title">Recommended PC</h3>
        <p className="pc-req-intro">
          You also need your own dump of the US disc (BLUS30011) and RPCS3 installed for the one-time decryption.
        </p>
        <div className="pc-req-grid">
          <div className="pc-col">
            <h4>Minimum</h4>
            <dl>
              <Row k="OS">Windows 10 or 11, 64-bit</Row>
              <Row k="Processor">Any 64-bit x86 CPU. Without AVX2, build the portable version.</Row>
              <Row k="Memory">Less than 16 GB works. The build runs fewer jobs at once.</Row>
              <Row k="Graphics">Any GPU with Direct3D 12</Row>
              <Row k="Storage">About 4 GB free while building</Row>
            </dl>
          </div>
          <div className="pc-col">
            <h4>Recommended</h4>
            <dl>
              <Row k="OS">Windows 11, 64-bit</Row>
              <Row k="Processor">An 8-core CPU with AVX2 and a fast single core (tested on an Intel Core i7-10700K)</Row>
              <Row k="Memory">16 GB or more</Row>
              <Row k="Graphics">A recent Direct3D 12 GPU (tested on an NVIDIA GeForce RTX 5070 Ti)</Row>
              <Row k="Storage">The install on the same NTFS drive as your disc dump, so game data is linked instead of copied</Row>
            </dl>
          </div>
        </div>
        <p className="caption spec-foot">
          On the recommended setup, P8Plus targets 120 fps with the High preset. The full list is in the{" "}
          <a href={asset("docs/#requirements")}>docs</a>.
        </p>
      </div>

      <div className="block-gap">
        <h3 className="h-cond sub-title">Port features</h3>
        <ul className="feature-list">
          {MODERN.map((m) => (
            <li key={m.title}>
              <m.icon size={22} strokeWidth={1.8} aria-hidden />
              <div>
                <b>{m.title}</b>
                <p>{m.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="block-gap" id="limitations">
        <div className="sub-head">
          <h3 className="h-cond sub-title">Limitations</h3>
          <p>
            If you hit something that isn&rsquo;t listed, <a href={links.issues} rel="noopener">open an issue</a> and
            attach the files from the install&rsquo;s <code>logs</code> folder.
          </p>
        </div>
        <div className="status-grid" style={{ "--cols": STATUS.length } as React.CSSProperties}>
          {STATUS.map((c) => (
            <div className="status-col" key={c.title}>
              <h3>
                <span className={`tag ${c.tone}`}>
                  <c.icon size={13} aria-hidden strokeWidth={2.4} />
                  {c.tone === "no" ? "No" : "Note"}
                </span>
                {c.title}
              </h3>
              <ul>
                {c.items.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="spec-row">
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  );
}
