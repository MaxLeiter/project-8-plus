import type { Metadata } from "next";
import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { Create } from "@/components/sections/Create";
import { Docs } from "@/components/sections/Docs";
import { FiveMinutes } from "@/components/sections/FiveMinutes";
import { Legal } from "@/components/sections/Legal";
import { WhatItIs } from "@/components/sections/WhatItIs";
import { links, reposPublic, site } from "@/content/site";
import { asset } from "@/lib/paths";

export const metadata: Metadata = {
  title: "Docs: Project 8 Plus",
  description:
    "Install guide, requirements and troubleshooting for Project 8 Plus, the modding guide for its creator tools, and the Lua API reference.",
  alternates: { canonical: `${site.url}/docs/` },
};

export default function DocsPage() {
  return (
    <>
      <Header
        repo={reposPublic ? links.repo : null}
        home={asset("")}
        nav={[
          { href: asset(""), label: "Home" },
          { id: "install", label: "Install" },
          { id: "troubleshooting", label: "Troubleshooting" },
          { id: "guide", label: "Modding guide" },
          { id: "api", label: "Lua API" },
          { id: "create", label: "Creator suite" },
        ]}
      />
      <main id="main">
        <Docs />
        {/* moved here from the home page on branch site-simple (depth lives on /docs) */}
        <Create />
        <FiveMinutes />
        <WhatItIs />
        <Legal />
      </main>
      <Footer />
    </>
  );
}
