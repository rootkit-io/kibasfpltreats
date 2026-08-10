import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
    // "bundler" moduleResolution (from tsconfig) doesn't add .ts automatically
    // in Vite's resolver — list extensions explicitly so bare specifiers resolve.
    extensions: [".ts", ".tsx", ".mts", ".js", ".mjs"],
    conditions: ["node"],
  },
});
