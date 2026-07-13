/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emits a minimal standalone server (node server.js) with only the
  // dependencies actually used, so the production Docker image doesn't need
  // to ship node_modules or run `next start` from the full source tree.
  output: "standalone",
};

export default nextConfig;
