// =====================================================================================================================
// PLACEHOLDER COPY FOR MAX TO REWRITE.
// Every sentence a visitor reads on the home page lives in this file, so the rewrite is one file. The facts behind each
// line (and their sources) are in ../../../FACTS.md; keep the facts, change the words. Keep it plain: no jargon
// (recompiler, PPU/SPU, vkd3d, PGO...), no hype voice. Depth goes to /docs.
// Search for "COPY(Max)" for spots that also need a decision, not just new words.
// =====================================================================================================================

export const draftBanner =
  "Draft for review. The words on this page are placeholders for Max to rewrite. Local preview only.";

export const hero = {
  // FACTS: What it is
  line: "Tony Hawk's Project 8 on PC. 60+ fps, HD textures and mods.",
  download: "Download for Windows",
  downloadSoon: "Download — coming soon",
  // download sizes live in FACTS.md (v0.9.1: windows zip 39,621,562 B, HD pack 45,627,999 B); the home page shows none
  // Items of the note under the download button; drawn with slanted separators (no "/" or "|" characters).
  downloadNote: ["Free", "Windows 10/11", "Unofficial fan project"],
  trailer: "Watch the trailer",
};

export const hd = {
  title: ["HD", "textures"],
  labels: ["Original", "HD"] as [string, string],
  // Capture runs 2026-10-18/20 (thp8-native/docs/shots/site_20261020): each pair is the same frozen frame, HD packs
  // off/on. Only pairs with a clear difference (>12% of pixels change); skater, distant views and menus barely
  // change (the textures are already near screen resolution there, and fonts are not upscaled), so they have no tab.
  pairs: [
    { id: "graffiti", tab: "Graffiti", off: "v4-hd-graffiti-off", on: "v4-hd-graffiti-on", alt: "Tagged concrete wall in Cretepark, close up", cap: "Cretepark: a tagged wall, close up (cropped, not enlarged)." },
    { id: "ground", tab: "Ground", off: "v4-hd-ground-off", on: "v4-hd-ground-on", alt: "Dirt, old tires and a grind rail in a Houses backyard", cap: "Houses: the backyard dirt, tires and rail." },
    { id: "rail", tab: "Rail", off: "v4-hd-rail-off", on: "v4-hd-rail-on", alt: "A grind rail over dirt and a rusty barrel lid, close up", cap: "Houses: a grind rail and a rusty barrel lid, close up." },
  ],
};

export const gallery = {
  title: ["In", "game"],
  // All from the 2026-10-18 capture run (HD on, current build). COPY(Max): no ultrawide shot yet (a 5120x1440 capture
  // needs the coordinator's OK); the first shot runs full width instead.
  shots: [
    { id: "v3-vista", alt: "Cretepark seen from high above: bowls, ramps and the city behind", cap: "Cretepark from above" },
    { id: "v3-air", alt: "Tony Hawk at the peak of a 900 over the Cretepark bowl", cap: "The 900 at Cretepark" },
    { id: "v4-grind", alt: "A frontside 50-50 grind on a backyard rail in Houses, with skate banners behind", cap: "FS 50-50 in Houses" },
    { id: "v4-cas", alt: "Create-a-Skater Plus body shape page: a full-body skater with shoulder size and depth sliders", cap: "Create-a-Skater Plus body shape" },
  ],
};

export const mods = {
  title: ["Mods", "included"],
  // Source: installer builder/thp8_builder/launcher/ws_setup.py:67-68 (defaults), addons.py, the shipped P8Plus ini.
  on: [
    { name: "Project 8 Plus", text: "60, 120 or 144 fps, and ultrawide support." },
    { name: "Create-a-Skater Plus", text: "Color any piece of clothing, set a skin tone, shape the body and face, and use every item, locked ones too." },
    { name: "HD textures", text: "Sharper world, skaters and menus, made on your PC in the background." },
    { name: "Smoothed skaters", text: "Rounder pro skaters and character heads, made from your game files during the install." },
    { name: "Button prompts", text: "Keyboard and controller support, with matching on-screen prompts." },
  ],
  onLabel: "On when you install",
  docsLink: "Make your own mods",
};

export const own = {
  title: "Do I need the game?",
  // Max's wording (2026-10-18).
  body: [
    "Yes. You need a ROM of the game (not provided here) and RPCS3, which is used to decrypt your copy during setup.",
    "It has to be the US PlayStation 3 version, BLUS30011.",
  ],
  // COPY(Max): link target for "how to copy your disc". RPCS3's quickstart covers dumping a disc.
  howTo: "How to copy your disc (RPCS3 guide)",
};

export const run = {
  title: "Will it run on my PC?",
  rows: [
    { k: "System", v: "Windows 10 or 11, 64-bit" },
    { k: "Graphics", v: "A graphics card with DirectX 12" },
    { k: "Memory", v: "16 GB recommended. Less works, the install just takes longer." },
    // Measured on the install-size branch `size` (coordinator 2026-10-18), pending the next release; see FACTS.md.
    { k: "Disk", v: "About 4 GB free to install (2 GB once installed)" },
    // COPY(Max): Max's line was "Works with Xbox, PlayStation and other controllers, plus keyboard". The game reads
    // Xbox-style (XInput) pads natively; PlayStation pads need Steam Input or DS4Windows today (FACTS.md), so this says so.
    { k: "Controllers", v: "Xbox controllers and keyboard. PlayStation and other controllers through Steam Input or DS4Windows." },
  ],
  // COPY(Max): no fps numbers on the home page (your 2026-10-08 call). Official numbers are in FACTS.md.
};

// Max's list (2026-10-18): no dates.
export const comingSoon = {
  title: "Coming soon",
  // The release ships an experimental Linux package: at launch (reposPublic) Linux moves from here to the Linux download link.
  // Steam Deck stays here (coordinator 2026-10-25); the release notes call the package "Linux (experimental)" only.
  items: ["Linux", "Steam Deck", "Mac", "Online play"],
  itemsAtLaunch: ["Steam Deck", "Mac", "Online play"],
};

export const linux = {
  // sizes are not shown on the home page (Max, 2026-10-25): the download shows them; FACTS.md keeps them
  link: "Linux (experimental)",
  // shown next to the Windows button only once the release is public
  note: "No HD textures on Linux yet.",
};

export const install = {
  title: "Install in 3 steps",
  steps: [
    {
      t: "Download and open",
      d: "Unzip the download anywhere and run THP8 Launcher.",
    },
    {
      t: "Point it at your game",
      d: "Pick your game folder, check that RPCS3 was found, and keep the default options.",
    },
    {
      t: "Build, then play",
      d: "Press Start building. It takes about 25 minutes, once. Then press Play.",
    },
  ],
};

export type QA = { q: string; a: string };

export const faq: QA[] = [
  // Max 2026-10-25. Facts: the builder translates the game's code and compiles a Windows exe on the player's PC; RPCS3 only
  // runs `--decrypt` during setup (installer README "Is this an emulator?", rpcs3.py; FACTS.md "What you need").
  { q: "Is this using an emulator?", a: "No. The launcher turns the game into a real PC program on your machine, so it runs natively, with no emulator while you play. RPCS3 is only used once, during setup, to decrypt your copy." },
  // Max's wording, word for word (2026-10-25). Facts behind it: installer README "Legal", FACTS.md "Legal points".
  { q: "Is this legal?", a: "Project 8 Plus includes and downloads none of the original game. You provide your own copy, which your own RPCS3 decrypts, and the launcher builds the PC copy on your PC. It's a free, unofficial fan project, not affiliated with Activision or Neversoft." },
  { q: "I have the European or Japanese disc. Will it work?", a: "No. Only the US disc, BLUS30011, works for now. The launcher checks your copy and tells you if it is a different one." },
  { q: "Does it matter which drive I install to?", a: "Installing on the same drive as your game files saves space. On a different drive the game data is copied, which needs about 5 GB more." },
  { q: "Where are my saves?", a: "In the install folder, under hdd0\\home\\00000001\\savedata. Copy that folder to back them up." },
  { q: "My antivirus or Windows warned me.", a: "The launcher and the game it builds are not signed, which these checks dislike. Download only from the official page, and report the warning so we can look into it." },
  // Max's wording, exactly (2026-10-24).
  { q: "Were language models used in the development?", a: "Yes, excessively." },
];

export type Issue = { t: string; d: string };

// Source: FACTS.md "Known issues". Max wants these fixed, not listed: delete an entry when its fix ships. When the list
// is empty the whole block (heading included) disappears and the FAQ takes the full width.
export const issues: Issue[] = [];

export const footer = {
  // Max's wording (2026-10-25). /docs keeps the fuller Legal section.
  legal: [
    "You need your own copy of Tony Hawk's Project 8 for PS3. Nothing from the game is included or downloaded.",
    "Unofficial and not affiliated with Activision, Neversoft, Tony Hawk or Sony. Tony Hawk's Project 8 is a trademark of Activision Publishing, Inc. PlayStation is a trademark of Sony Interactive Entertainment.",
    "Our code and art are MIT-licensed. Third-party parts keep their own licenses.",
  ],
  credits: "Credits and full licenses",
};
