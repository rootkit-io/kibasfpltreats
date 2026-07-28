/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for the Docker
  // runtime stage -- required by frontend/Dockerfile.
  output: "standalone",
};

module.exports = nextConfig;
