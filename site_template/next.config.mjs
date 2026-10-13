/** @type {import('next').NextConfig} */
const nextConfig = {
  // Do NOT map SITE_CONFIG through next.config `env` — that inlines the value
  // at build time and breaks per-lead deploys. Server components read
  // process.env.SITE_CONFIG at request time (see lib/site-config.js).
};

export default nextConfig;
