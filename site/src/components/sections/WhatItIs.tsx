import { Cpu, Disc3, Gauge, PenTool } from "lucide-react";
import { SectionHead } from "../SectionHead";

const STEPS = [
  {
    icon: Disc3,
    title: "The disc dump",
    text: "A folder dump of the US PS3 disc, BLUS30011 version 01.02, in the layout RPCS3 uses.",
    where: "The launcher never writes to it",
  },
  {
    icon: Cpu,
    title: "The builder",
    text: "It checks the dump and has RPCS3 decrypt the game's executable. Then it translates the code to C/C++ and compiles thp8.exe, which takes about 20 to 25 minutes.",
    where: "Runs once",
  },
  {
    icon: Gauge,
    title: "Project 8 Plus",
    text: "A plugin that raises the frame rate and adds Hor+ widescreen and Xbox button prompts. HD textures and smoothed skaters are generated locally.",
    where: "Play original starts the unmodified game",
  },
  {
    icon: PenTool,
    title: "Create",
    text: "A mod manager, the Texture Studio and a 3D level editor. Goals, people, stories and Lua scripts are made in the editor, and the live link sends edits to the running game.",
    where: "A mod contains only its author's files",
  },
];

export function WhatItIs() {
  return (
    <section className="section wrap" id="what" aria-labelledby="what-title">
      <SectionHead
        id="what-title"
        title={
          <>
            How it <span className="pink">works.</span>
          </>
        }
        lede={
          <>
            The base is a native port of the game, compiled on your PC. P8Plus is a plugin that changes how it looks
            and runs. The creator tools for making mods live in the same launcher.
          </>
        }
      />
      <ol className="flow" aria-label="How Project 8 Plus is built, in four steps">
        {STEPS.map((s) => (
          <li className="flow-step" key={s.title}>
            <div className="n" aria-hidden>
              <s.icon size={26} strokeWidth={1.8} />
            </div>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
            <p className="where">{s.where}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
