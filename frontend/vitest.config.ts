import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Les tests vivent à côté du code qu'ils vérifient, dans `src/`. L'environnement `node` suffit :
// les composants sont rendus en HTML statique avec `react-dom/server`, sans DOM simulé.
export default defineConfig({
  resolve: {
    // Même alias que `tsconfig.json` (`@/*` → `src/*`), que Vite ne lit pas de lui-même.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
});
