// Variables d'environnement connues du frontend. Les déclarer ici permet l'accès littéral
// `process.env.NEXT_PUBLIC_API_BASE_URL`, que Next.js remplace par sa valeur au build, tout en
// gardant l'option `noPropertyAccessFromIndexSignature` pour toutes les autres clés.
declare namespace NodeJS {
  interface ProcessEnv {
    readonly NEXT_PUBLIC_API_BASE_URL?: string;
  }
}
