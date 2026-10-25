import { ArrowRight } from "lucide-react";
import { SectionHead } from "../SectionHead";
import { loadApi, loadGuide } from "@/lib/docs";
import { asset } from "@/lib/paths";

/** The home page's pointer to /docs. */
export async function DocsTeaser() {
  const guide = await loadGuide();
  const api = loadApi();
  const links = [
    { href: "docs/#install", title: "Install guide", text: "From a disc dump to the Play button, in seven steps." },
    { href: "docs/#troubleshooting", title: "Troubleshooting", text: "Rejected game folders, RPCS3, the build, sound and controllers." },
    { href: "docs/#guide", title: "Modding guide", text: `${guide.length} pages, from a first mod to the game's file formats.` },
    { href: "docs/#api", title: "Lua API reference", text: `${api.entries.length} entries, searchable.` },
  ];
  return (
    <section className="section wrap" id="docs" aria-labelledby="docs-title">
      <SectionHead
        id="docs-title"
        title={
          <>
            The <span className="pink">docs.</span>
          </>
        }
        lede={<>Installing, fixing and modding are covered on the docs page, which has its own contents and search.</>}
      />
      <ul className="teaser">
        {links.map((l) => (
          <li key={l.href}>
            <a href={asset(l.href)}>
              <b>{l.title}</b>
              <span>{l.text}</span>
              <ArrowRight size={18} aria-hidden />
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
