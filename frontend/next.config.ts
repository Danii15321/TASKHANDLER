import type { NextConfig } from "next";

import { assertOnlyAllowedPublicVariables, loadPublicConfig } from "./src/config/public-config";
import { buildSecurityHeaders } from "./src/security/security-headers";

// Ce fichier est évalué au build, au démarrage de `next start` et de `next dev` : une
// configuration publique invalide arrête donc l'application avant qu'elle ne serve une page.
assertOnlyAllowedPublicVariables(process.env);
loadPublicConfig();

const securityHeaders = buildSecurityHeaders({
  isProduction: process.env.NODE_ENV === "production",
});

const nextConfig: NextConfig = {
  // Le mode strict de React exécute deux fois certains rendus en développement pour révéler
  // les effets de bord indésirables.
  reactStrictMode: true,
  // N'annonce pas le framework (`X-Powered-By: Next.js`) : inutile de faciliter le repérage.
  poweredByHeader: false,
  // Les source maps du navigateur révéleraient le code source complet : jamais publiées.
  productionBrowserSourceMaps: false,

  // requires : rien.
  // assigns  : rien.
  // ensures  : applique les en-têtes de sécurité fixes à toutes les routes, fichiers statiques
  //            compris.
  headers() {
    return Promise.resolve([{ source: "/:path*", headers: securityHeaders }]);
  },
};

export default nextConfig;
