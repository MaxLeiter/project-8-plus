import type { NextConfig } from "next";

// BASE_PATH: "" for a subdomain (default), "/project8plus" (or any path) when the site is served under a path of
// another domain. See README.md, "Base path". MEDIA_BASE_URL: optional absolute URL of the encoded media.
const basePath = (process.env.BASE_PATH ?? "").replace(/\/$/, "");

const config: NextConfig = {
  output: "export",
  basePath: basePath || undefined,
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_MEDIA_BASE_URL: process.env.MEDIA_BASE_URL ?? "",
  },
};

export default config;
