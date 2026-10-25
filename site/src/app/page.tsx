import { Header } from "@/components/Header";
import { Basics, FaqIssues, Gallery, HdTextures, Intro, LegalFooter, Mods } from "@/components/simple/Sections";
import { links, reposPublic } from "@/content/site";
import { asset } from "@/lib/paths";
import "./simple.css";

// The simple home page for first-time visitors (Max's 2026-10-18 order): intro, HD textures, shots, mods, then the
// practical bits (own the game, will it run, install, FAQ + known issues) and the legal footer. Depth lives on /docs.
export default function Page() {
  return (
    <>
      <Header
        repo={reposPublic ? links.repo : null}
        home="#top"
        nav={[
          { id: "hd", label: "HD textures" },
          { id: "mods", label: "Mods" },
          { id: "basics", label: "Install" },
          { id: "faq", label: "FAQ" },
          { href: asset("docs/"), label: "Docs" },
        ]}
      />
      <main id="main">
        <Intro />
        <HdTextures />
        <Gallery />
        <Mods />
        <Basics />
        <FaqIssues />
      </main>
      <LegalFooter />
    </>
  );
}
