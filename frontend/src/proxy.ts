// Proxy Next.js : s'exécute avant le rendu de chaque page.
//
// Son seul rôle est de poser la CSP à nonce. Il ne prend aucune décision d'autorisation : un
// contrôle d'accès dans le proxy se contourne (CVE-2025-29927), et seule l'API fait foi.
import { NextResponse, type NextRequest } from "next/server";

import { loadPublicConfig } from "@/config/public-config";
import { buildContentSecurityPolicy, createNonce } from "@/security/content-security-policy";

// Lue une fois au chargement : une configuration invalide empêche de servir la moindre page.
const publicConfig = loadPublicConfig();
const isDevelopment = process.env.NODE_ENV === "development";

// requires : `request` est la requête entrante, fournie par Next.js.
// assigns  : rien (aucun état global ; seuls les en-têtes de cette requête sont modifiés).
// ensures  : la requête transmise au rendu et la réponse portent la même CSP, avec un nonce
//            neuf. Next.js lit ce nonce dans l'en-tête de la requête et l'ajoute à ses scripts.
export function proxy(request: NextRequest): NextResponse {
  const nonce = createNonce();
  const policy = buildContentSecurityPolicy({
    nonce,
    apiOrigin: publicConfig.apiOrigin,
    isDevelopment,
  });

  // `set` remplace toute CSP envoyée par le client : un attaquant ne peut pas imposer son
  // propre nonce au rendu.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

// Toutes les routes sauf les fichiers statiques (JavaScript, CSS, images), qui ne sont pas des
// documents et n'exécutent rien par eux-mêmes. Contrairement à l'exemple de la documentation,
// les préchargements (`next-router-prefetch`) ne sont pas exclus : aucune réponse rendue ne
// doit pouvoir sortir sans CSP posée par le serveur.
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
