// Everything on the page that is a fact, a number or a link lives here or in ../docs (rendered at build time).
// Placeholders are marked TODO and listed in README.md ("Before the first deploy").

// The repositories, releases and issue tracker are not public yet. While false, the pages show no links to them:
// download buttons read "coming soon", repo references in the docs render as plain text. Flip to true at launch.
export const reposPublic = false;

export const links = {
  // The project's repository (Max, 2026-10-24); private until launch, so every link to it waits for reposPublic.
  // modRepo/modBranch: the docs' "view source" links (lib/docs.ts) -- check the paths match the public layout at launch.
  repo: "https://github.com/MaxLeiter/project-8-plus",
  modRepo: "https://github.com/MaxLeiter/project-8-plus",
  modBranch: "master",
  nativeRepo: "https://github.com/MaxLeiter/project-8-plus",
  releases: "https://github.com/MaxLeiter/project-8-plus/releases",
  issues: "https://github.com/MaxLeiter/project-8-plus/issues",
  ps3recomp: "https://github.com/sp00nznet/ps3recomp",
  rpcs3: "https://rpcs3.net",
  hofmannModels: "https://github.com/Phhofm/models",
  ccBy4: "https://creativecommons.org/licenses/by/4.0/",
  apache2: "https://www.apache.org/licenses/LICENSE-2.0",
  mit: "https://opensource.org/license/mit",
};

export const site = {
  name: "Project 8 Plus",
  title: "Project 8 Plus: a native PC port of Tony Hawk's Project 8",
  description:
    "Tony Hawk's Project 8, running natively on Windows. The launcher builds the game from a dump of your PS3 disc. P8Plus raises the frame rate and adds HD textures and ultrawide support, and the level editor can change a park while the game runs.",
  // The public origin (absolute social-card links). The user chose the subdomain project8.maxleiter.com, base path "/".
  url: process.env.SITE_URL ?? "https://project8.maxleiter.com",
};

