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
  kicker: "Unofficial fan project",
  // FACTS: What it is
  line: "Tony Hawk's Project 8 on PC. 60+ fps, HD textures and mods.",
  download: "Download for Windows",
  downloadSoon: "Download — coming soon",
  // COPY(Max): add the zip size at the release (repin27/28 zips are ~93 MB).
  downloadNote: "Free · Windows 10/11",
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
    { id: "ground", tab: "Ground", off: "v4-hd-ground-off", on: "v4-hd-ground-on", alt: "Dirt, old tyres and a grind rail in a Houses backyard", cap: "Houses: the backyard dirt, tyres and rail." },
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
    { name: "Create-a-Skater Plus", text: "Colour any piece of clothing, set a skin tone, shape the body and face, and use every item, locked ones too." },
    { name: "HD textures", text: "Sharper world, skaters and menus, made on your PC in the background." },
    { name: "Smoothed skaters", text: "Rounder pro skaters and character heads, made from your game files during the install." },
    { name: "Button prompts", text: "Keyboard and controller support, with matching on-screen prompts." },
  ],
  onLabel: "On when you install",
  original:
    "The Original look switch turns off just the HD textures and smoothed skaters, and keeps the frame rate and widescreen.",
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
  items: ["Linux", "Steam Deck", "Mac", "Online play"],
};

export const install = {
  title: "Install in 3 steps",
  steps: [
    {
      t: "Download and open",
      d: "Unzip the download anywhere and run THP8 Launcher. If Windows says it protected your PC, choose More info, then Run anyway.",
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
  // Max 2026-10-24: facts only, no legal claim (installer README "Legal", FACTS.md "Legal points").
  { q: "Is this legal?", a: "Project 8 Plus includes none of the game: no game files, code or keys, and it downloads none. You use your own copy, which your own RPCS3 decrypts, and the launcher builds the PC version from it on your PC, where it stays. It's a free, unofficial fan project, not affiliated with Activision or Neversoft." },
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
  legal: [
    "You must own Tony Hawk's Project 8 for PlayStation 3 to use this project. The download contains no game files: no disc image, no game program, no extracted assets and no keys. Everything made from the game is made on your PC, from your copy, and may not be shared.",
    "Project 8 Plus is a free, unofficial fan project. It is not affiliated with, sponsored by or endorsed by Activision, Neversoft, Tony Hawk, Sony or any other rights holder. Tony Hawk's Project 8 is a trademark of Activision Publishing, Inc. Tony Hawk is a trademark of Tony Hawk, Inc. The game was developed by Neversoft Entertainment. PlayStation and PS3 are trademarks of Sony Interactive Entertainment Inc. Screenshots and footage of the game are shown to document the project.",
    "This project's own code and art are under the MIT licence. Third-party parts keep their own licences.",
  ],
  credits: "Credits and full licences",
};
