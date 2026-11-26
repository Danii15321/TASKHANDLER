// Vérifie que la chaîne d'outils du frontend est correctement en place.
import { readFileSync } from "node:fs";

import { expect, test } from "vitest";

// Le fichier `.nvmrc` se trouve à la racine de `frontend/`, un niveau au-dessus de `src/`.
const NODE_VERSION_FILE = new URL("../.nvmrc", import.meta.url);

// requires : le fichier `frontend/.nvmrc` existe et contient une version exacte de Node.js.
// assigns  : rien (lecture seule du fichier).
// ensures  : réussit seulement si Node.js est exactement la version épinglée.
test("Node.js correspond à la version épinglée", () => {
  // Une autre version signifierait que les tests ne s'exécutent pas dans l'environnement
  // prévu : leurs résultats ne seraient alors plus fiables.
  const pinnedVersion = readFileSync(NODE_VERSION_FILE, "utf8").trim();

  expect(process.versions.node).toBe(pinnedVersion);
});
