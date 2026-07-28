/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for the Docker
  // runtime stage -- same Phase 9 architecture as apps/admin.
  output: "standalone",
};

module.exports = nextConfig;
