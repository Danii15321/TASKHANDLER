// En-têtes de sécurité fixes, ajoutés à toutes les réponses (pages, fichiers statiques, erreurs).
//
// La CSP n'en fait pas partie : elle contient le nonce, propre à chaque requête, et est posée
// par `src/proxy.ts`. Ces en-têtes-ci ne dépendent pas de la requête.

// En-tête HTTP, au format attendu par la fonction `headers()` de `next.config.ts`.
export interface HttpHeader {
  readonly key: string;
  readonly value: string;
}

// Deux ans, sous-domaines compris, comme l'API : un navigateur qui a vu ce domaine en HTTPS
// refusera ensuite toute connexion en clair.
const HSTS_VALUE = "max-age=63072000; includeSubDomains";

// Fonctions du navigateur dont l'application n'a aucun besoin : désactivées pour la page et
// pour tout contenu qu'elle intégrerait.
const PERMISSIONS_POLICY = [
  "accelerometer=()",
  "browsing-topics=()",
  "camera=()",
  "display-capture=()",
  "geolocation=()",
  "gyroscope=()",
  "magnetometer=()",
  "microphone=()",
  "payment=()",
  "usb=()",
].join(", ");

// requires : `isProduction` est vrai seulement pour un build de production.
// assigns  : rien (fonction pure).
// ensures  : renvoie la liste des en-têtes de sécurité fixes ; HSTS n'y figure qu'en production.
export function buildSecurityHeaders(options: { readonly isProduction: boolean }): HttpHeader[] {
  const headers: HttpHeader[] = [
    // Le navigateur ne devine jamais le type d'un contenu : un texte ne devient pas un script.
    { key: "X-Content-Type-Options", value: "nosniff" },
    // Aucune adresse n'est transmise aux sites suivants. Les liens reçus par e-mail portent un
    // jeton dans le fragment : même si le fragment n'est jamais envoyé, on ne prend aucun risque.
    { key: "Referrer-Policy", value: "no-referrer" },
    // Doublon de `frame-ancestors 'none'` pour les navigateurs anciens.
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: PERMISSIONS_POLICY },
    // Une fenêtre ouverte depuis un autre site ne garde aucune référence vers la nôtre.
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    // Un autre site ne peut pas inclure nos ressources pour les lire par un canal auxiliaire.
    { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  ];
  if (options.isProduction) {
    headers.push({ key: "Strict-Transport-Security", value: HSTS_VALUE });
  }
  return headers;
}
