// Configuration ESLint du frontend (format « flat config »).
//
// Trois couches s'empilent :
//   1. les règles de Next.js (React, hooks, accessibilité jsx-a11y, imports, Core Web Vitals) ;
//   2. les règles typées et strictes de typescript-eslint, qui s'appuient sur le compilateur ;
//   3. nos interdictions de sécurité, qui traduisent frontend/CLAUDE.md en contrôles automatiques.
import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import tseslint from "typescript-eslint";

// Next.js n'active qu'une poignée de règles d'accessibilité. On reprend le jeu strict de
// jsx-a11y (cible WCAG 2.2 AA) à partir du plugin que Next.js charge déjà : même instance,
// aucune dépendance supplémentaire.
const jsxA11y = nextCoreWebVitals.find((config) => config.plugins?.["jsx-a11y"]).plugins[
  "jsx-a11y"
];

// Message commun : le stockage persistant du navigateur est lisible par tout script injecté.
const STORAGE_MESSAGE =
  "Aucune donnée sensible dans le stockage du navigateur : un jeton y serait lisible par une XSS.";
// Message commun : injecter du HTML brut contourne l'échappement automatique de React.
const HTML_MESSAGE = "Injection de HTML brut interdite : React échappe le texte, gardez ce rôle.";

export default defineConfig([
  // Fichiers générés ou installés : jamais analysés.
  globalIgnores([".next/**", "node_modules/**", "coverage/**", "next-env.d.ts"]),

  ...nextCoreWebVitals,
  ...nextTypescript,

  {
    // Accessibilité : toutes les règles strictes de jsx-a11y, en erreur.
    rules: jsxA11y.flatConfigs.strict.rules,
  },

  {
    // Une directive `eslint-disable` devenue inutile est une erreur : une exception ne doit
    // pas survivre à la raison qui la justifiait.
    linterOptions: {
      reportUnusedDisableDirectives: "error",
      reportUnusedInlineConfigs: "error",
    },
  },

  {
    // Règles typées : elles lisent les types calculés par TypeScript, d'où `projectService`.
    files: ["**/*.ts", "**/*.tsx"],
    extends: [tseslint.configs.strictTypeChecked, tseslint.configs.stylisticTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // Équivalent du typage complet exigé côté backend : chaque fonction annonce ce qu'elle
      // renvoie, ce qui rend son contrat (`ensures`) vérifiable par le compilateur.
      "@typescript-eslint/explicit-function-return-type": ["error", { allowExpressions: true }],
      // `@ts-ignore` et `@ts-nocheck` sont interdits ; `@ts-expect-error` exige une justification.
      "@typescript-eslint/ban-ts-comment": [
        "error",
        {
          "ts-expect-error": "allow-with-description",
          "ts-ignore": true,
          "ts-nocheck": true,
          minimumDescriptionLength: 10,
        },
      ],
    },
  },

  {
    // Interdictions de sécurité, appliquées à tout le code.
    rules: {
      // Exécution de code construit à partir d'une chaîne : porte d'entrée d'une XSS.
      "no-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      // `dangerouslySetInnerHTML` et les liens `javascript:` désactivent la protection de React.
      "react/no-danger": "error",
      "react/jsx-no-script-url": "error",
      // `target="_blank"` sans `rel="noreferrer"` donne à la page ouverte un accès à celle-ci.
      "react/jsx-no-target-blank": "error",
      "react/iframe-missing-sandbox": "error",
      // Le jeton d'accès vit en mémoire uniquement (frontend/CLAUDE.md, « Session et jetons »).
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: STORAGE_MESSAGE },
        { name: "sessionStorage", message: STORAGE_MESSAGE },
        { name: "indexedDB", message: STORAGE_MESSAGE },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: STORAGE_MESSAGE },
        { object: "window", property: "sessionStorage", message: STORAGE_MESSAGE },
        { object: "window", property: "indexedDB", message: STORAGE_MESSAGE },
        {
          object: "document",
          property: "cookie",
          message: "Le cookie de session est HttpOnly : le frontend n'a jamais à lire un cookie.",
        },
        { object: "document", property: "write", message: HTML_MESSAGE },
        { object: "document", property: "writeln", message: HTML_MESSAGE },
        { property: "innerHTML", message: HTML_MESSAGE },
        { property: "outerHTML", message: HTML_MESSAGE },
        { property: "insertAdjacentHTML", message: HTML_MESSAGE },
      ],
      // Pas de `console` : un journal du navigateur finit souvent par contenir un jeton.
      "no-console": "error",
    },
  },
]);
