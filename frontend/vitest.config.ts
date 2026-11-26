import { defineConfig } from "vitest/config";

// Les tests vivent à côté du code qu'ils vérifient, dans `src/`. L'environnement `node` suffit
// tant qu'aucun composant n'est rendu ; un environnement DOM sera ajouté par la phase qui en a
// besoin.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
});
