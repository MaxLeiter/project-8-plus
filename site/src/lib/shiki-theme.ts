import type { ThemeRegistrationRaw } from "shiki";

// Code colours from the p8ui palette (thp8-native builder/p8ui/tokens.py: SERIES, LOG), tuned for AA contrast on
// the code background (FIELD #10131a).
export const p8Theme: ThemeRegistrationRaw = {
  name: "p8ui",
  type: "dark",
  colors: { "editor.background": "#10131a", "editor.foreground": "#e8e8ec" },
  settings: [
    { settings: { background: "#10131a", foreground: "#e8e8ec" } },
    { scope: ["comment", "punctuation.definition.comment"], settings: { foreground: "#8a90a0", fontStyle: "italic" } },
    { scope: ["keyword", "storage", "keyword.operator.logical", "keyword.control"], settings: { foreground: "#ff7a9a" } },
    { scope: ["string", "string.quoted", "punctuation.definition.string"], settings: { foreground: "#6fd3a5" } },
    { scope: ["constant.numeric", "constant.language", "constant.character", "support.constant"],
      settings: { foreground: "#f0c070" } },
    { scope: ["entity.name.function", "support.function", "meta.function-call", "variable.function"],
      settings: { foreground: "#8cc0fb" } },
    { scope: ["support.type.property-name", "meta.object-literal.key", "entity.name.tag.yaml", "support.type.property-name.json"],
      settings: { foreground: "#5eead4" } },
    { scope: ["variable.language", "support.class", "entity.name.type", "entity.name.class"],
      settings: { foreground: "#c4b2fc" } },
    { scope: ["punctuation", "meta.brace", "keyword.operator"], settings: { foreground: "#a2a8b6" } },
  ],
};
