import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [],
  },
  turbopack: {
    root: process.cwd(),
  },
  experimental: {
    // typedRoutes: true,  // enable if you want typed link hrefs
  },
}

export default nextConfig
