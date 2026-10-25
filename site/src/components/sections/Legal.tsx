import { Scale } from "lucide-react";
import { SectionHead } from "../SectionHead";
import { links } from "@/content/site";

export function Legal() {
  return (
    <section className="section wrap" id="legal" aria-labelledby="legal-title">
      <SectionHead id="legal-title" title={<>Legal <span className="pink">notices.</span></>} />
      <div className="legal-grid">
        <div className="legal-text">
          <div className="callout legal">
            <Scale size={18} aria-hidden />
            <p>
              No game files are distributed, and you must own Tony Hawk&rsquo;s Project 8 for PlayStation 3. The
              builder makes everything derived from the game locally. This project contains no disc image, game
              executable, decrypted binary, extracted asset or decryption key, and no translation of the game&rsquo;s
              code.
            </p>
          </div>
          <h3>Not affiliated</h3>
          <p>
            Project 8 Plus is an independent, unofficial fan project. It is not affiliated with, sponsored by or endorsed
            by Activision, Neversoft, Tony Hawk, Sony, or any other rights holder. Tony Hawk&rsquo;s Project 8 is a
            trademark of Activision Publishing, Inc.; Tony Hawk is a trademark of Tony Hawk, Inc.; the game was developed
            by Neversoft Entertainment. PlayStation and PS3 are trademarks of Sony Interactive Entertainment Inc. All
            trademarks and copyrights belong to their owners. Screenshots and footage of the game are shown to document
            the project.
          </p>
          <h3>Free</h3>
          <p>Project 8 Plus is non-commercial and accepts no payment.</p>
          <h3>Licence</h3>
          <p>
            The project&rsquo;s own code, patches, tools, artwork and documentation are under the{" "}
            <a href={links.mit} rel="noopener">MIT licence</a>, and third-party components keep their own licences.
            The licence doesn&rsquo;t cover anything derived from the game. Files the tools build from the game
            aren&rsquo;t licensed by this project and may not be redistributed.
          </p>
          <h3>Game files</h3>
          <p>
            Requests for the game, or for files from it, are closed without an answer. Contributions containing game
            data, decrypted binaries, keys or translated code are rejected.
          </p>
        </div>

        <div>
          <h3 className="h-cond sub-title">Credits</h3>
          <ul className="credits">
            <li>
              <b>
                <a href={links.ps3recomp} rel="noopener">ps3recomp</a>
              </b>{" "}
              by Ned Heller (sp00nznet) and contributors
              <span className="what">The static recompiler and runtime the port is built on (MIT).</span>
            </li>
            <li>
              <b>gh3</b>, sp00nznet&rsquo;s Guitar Hero III port, and <b>Project8Recomp</b>, the Xbox 360 recompilation
              <span className="what">The template for this port, and the model for its setup flow and legal framing.</span>
            </li>
            <li>
              <b>
                <a href={links.rpcs3} rel="noopener">RPCS3</a>
              </b>
              <span className="what">Decrypts your copy for the builder. Project 8 Plus began as a patch for it.</span>
            </li>
            <li>
              <b>4xTextureDAT2_otf</b> by Philip Hofmann (
              <a href={links.hofmannModels} rel="noopener">Phhofm</a>),{" "}
              <a href={links.ccBy4} rel="noopener">CC BY 4.0</a>
              <span className="what">
                The upscaling model behind the HD textures, run on your PC. Network architecture: DAT (Dual Aggregation
                Transformer) by Zheng Chen et al., ICCV 2023, <a href={links.apache2} rel="noopener">Apache License 2.0</a>.
              </span>
            </li>
            <li>
              <b>ONNX Runtime</b> (MIT) with <b>DirectML</b> (Microsoft&rsquo;s DirectML licence)
              <span className="what">Runs the upscaling model on your graphics card. Both ship with the HD textures tool.</span>
            </li>
            <li>
              <b>&ldquo;Your Game, Your Rules&rdquo;</b>, original score for Project 8 Plus,{" "}
              <a href={links.ccBy4} rel="noopener">CC BY 4.0</a>
              <span className="what">The trailer music, synthesized from code: no samples, no game audio.</span>
            </li>
            <li>
              <b>Lua 5.4</b>, <b>zig</b>, <b>LLVM</b>, <b>mingw-w64</b>, <b>CPython</b>, <b>Dear ImGui</b> (imgui-bundle)
              <span className="what">The scripting language, the toolchain the builder uses, and the launcher&rsquo;s UI.</span>
            </li>
            <li>
              <b>Inter</b>, <b>Barlow Condensed</b>, <b>JetBrains Mono</b> (SIL OFL 1.1) and <b>Lucide</b> icons (ISC)
              <span className="what">The launcher&rsquo;s type and icons, used on this page too.</span>
            </li>
            <li>
              <b>Neversoft and Activision</b>
              <span className="what">For the game.</span>
            </li>
          </ul>
        </div>
      </div>
    </section>
  );
}
