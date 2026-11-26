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
    coverage: {
      provider: "v8",
      // Tout le code de `src/` est mesuré, y compris un fichier qu'aucun test n'importe : sans
      // cette liste, un module oublié n'apparaîtrait pas dans le rapport et ne ferait pas baisser
      // le taux.
      include: ["src/**/*.{ts,tsx}"],
      // Les tests eux-mêmes et les déclarations de types ne contiennent aucun code exécutable.
      exclude: ["src/**/*.test.{ts,tsx}", "src/**/*.d.ts"],
      reporter: ["text", "text-summary"],
      // Même seuil que le backend, branches comprises : sous ces valeurs, la commande échoue et
      // la CI bloque le changement.
      thresholds: {
        lines: 95,
        statements: 95,
        functions: 95,
        branches: 95,
      },
    },
  },
});
