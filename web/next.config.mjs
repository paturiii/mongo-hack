// The browser talks to the gateway through same-origin rewrites, so the session cookie is first-party.
// Live board events (SSE) go straight to the gateway (NEXT_PUBLIC_API_BASE_URL) to avoid proxy buffering.
const GATEWAY = process.env.GATEWAY_URL || 'http://localhost:8000';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {source: '/api/:path*', destination: `${GATEWAY}/api/:path*`},
      {source: '/auth/:path*', destination: `${GATEWAY}/auth/:path*`},
    ];
  },
};

export default nextConfig;
