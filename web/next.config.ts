import type { NextConfig } from "next";

// The desktop app serves the built files itself (pigeon/desktop.py), so this is a static export: no Node at runtime.
// In `npm run dev` the page runs on :3000 and /api is forwarded to a running `pigeon app --no-window --port 4472`.
const dev = process.env.NODE_ENV === "development";

const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  ...(dev
    ? {
        async rewrites() {
          const api = process.env.PIGEON_API ?? "http://127.0.0.1:4472";
          return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
        },
      }
    : {}),
};

export default config;
