import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Lightbox } from "@/components/Lightbox";
import { site } from "@/content/site";
import { image } from "@/lib/media";
import { media } from "@/lib/paths";

// The launcher's fonts (p8ui), subset to Latin by scripts/fonts.py. SIL OFL 1.1, see src/fonts/LICENSES.md.
// Four files on first load: Inter 400 and 600 (also standing in for 500 and 700), Barlow Condensed 700 (also for
// 600) and 800. JetBrains Mono loads only where code is shown.
const inter = localFont({
  src: [
    { path: "../fonts/inter-400.woff", weight: "400", style: "normal" },
    { path: "../fonts/inter-600.woff", weight: "500 700", style: "normal" },
  ],
  variable: "--font-inter",
  display: "swap",
});

const barlow = localFont({
  src: [
    { path: "../fonts/barlow-condensed-700.woff", weight: "600 700", style: "normal" },
    { path: "../fonts/barlow-condensed-800.woff", weight: "800", style: "normal" },
  ],
  variable: "--font-barlow",
  display: "swap",
});

const jetbrains = localFont({
  src: [{ path: "../fonts/jetbrains-mono-400.woff", weight: "400 700", style: "normal" }],
  variable: "--font-jetbrains",
  display: "swap",
  preload: false,
});

const og = image("og").files[0];

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: site.title,
  description: site.description,
  applicationName: site.name,
  openGraph: {
    type: "website",
    title: site.title,
    description: site.description,
    siteName: site.name,
    images: [{ url: media(og.src), width: og.w, height: Math.round((og.w * 672) / 1280), alt: "Project 8 Plus: native PC port, level editor, Lua mods, HD textures" }],
  },
  twitter: { card: "summary_large_image", title: site.title, description: site.description, images: [media(og.src)] },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: "#0d0f15",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${barlow.variable} ${jetbrains.variable}`}>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        {children}
        <Lightbox />
      </body>
    </html>
  );
}
