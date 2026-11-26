import type { NextConfig } from "next";

// Configuration minimale de la phase 0. Les en-têtes de sécurité (CSP à nonce, HSTS…) et
// `poweredByHeader: false` arrivent en phase 1, avec les tests qui les prouvent.
const nextConfig: NextConfig = {
  // Le mode strict de React exécute deux fois certains rendus en développement pour révéler
  // les effets de bord indésirables.
  reactStrictMode: true,
};

export default nextConfig;
