import { defineConfig } from "vitest/config";

// Deliberately separate from vite.config.ts. The reducer is plain TypeScript
// over plain data -- no React, no DOM -- which is the point of it, so its tests
// need neither the react plugin nor a browser environment.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
