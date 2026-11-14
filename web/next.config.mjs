// The browser talks to the gateway through same-origin rewrites, so the session cookie is first-party.
// Live board events (SSE): app/api/boards/[slug]/events/route.ts streams them same-origin (no buffering).
const GATEWAY = process.env.GATEWAY_URL || process.env.API_BASE_URL || 'http://localhost:8000';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  devIndicators: false, // hide the Next.js "N" badge in dev (demo-clean)
  async rewrites() {
    return [
      {source: '/api/:path*', destination: `${GATEWAY}/api/:path*`},
      {source: '/auth/:path*', destination: `${GATEWAY}/auth/:path*`},
      {source: '/media/:path*', destination: `${GATEWAY}/media/:path*`}, // Track 1 voice briefings
    ];
  },
};

export default nextConfig;
