// Politique de sécurité du contenu (CSP) à nonce.
//
// La CSP est la défense principale contre l'injection de script (XSS). Le jeton d'accès vit en
// mémoire JavaScript : un script injecté pourrait le lire. On n'autorise donc que les scripts
// qui portent le nonce de la requête, un secret aléatoire neuf à chaque page servie, qu'un
// attaquant ne peut pas deviner.

// Taille du nonce : 128 bits d'aléa, le minimum recommandé par la spécification CSP.
const NONCE_BYTES = 16;

// Paramètres de la politique.
export interface ContentSecurityPolicyOptions {
  // Nonce de la requête, produit par `createNonce`.
  readonly nonce: string;
  // Origine de l'API, seule destination autorisée pour les appels réseau en dehors du site.
  readonly apiOrigin: string;
  // Vrai sous `next dev` : React a alors besoin d'`eval` pour reconstruire les piles d'erreurs.
  readonly isDevelopment: boolean;
}

// requires : rien (l'API Web Crypto est disponible dans Node.js et dans le navigateur).
// assigns  : rien (consomme seulement de l'aléa du générateur cryptographique du système).
// ensures  : renvoie 16 octets aléatoires encodés en base64 (24 caractères), différents à
//            chaque appel avec une probabilité écrasante.
export function createNonce(): string {
  // `crypto.getRandomValues`, et jamais `Math.random` : seul un générateur cryptographique
  // produit une valeur imprévisible pour un attaquant.
  const bytes = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  return btoa(String.fromCharCode(...bytes));
}

// requires : `options.nonce` provient de `createNonce` ; `options.apiOrigin` est une origine
//            validée par `parsePublicConfig`.
// assigns  : rien (fonction pure).
// ensures  : renvoie la valeur de l'en-tête `Content-Security-Policy`, directives séparées par
//            « ; ». En production, aucun script sans nonce ni `eval` n'est permis.
export function buildContentSecurityPolicy(options: ContentSecurityPolicyOptions): string {
  const { nonce, apiOrigin, isDevelopment } = options;
  const nonceSource = `'nonce-${nonce}'`;

  const directives: readonly (readonly string[])[] = [
    // Refus par défaut : toute ressource non listée ci-dessous est bloquée.
    ["default-src", "'none'"],
    // Seuls les scripts portant le nonce s'exécutent. `'strict-dynamic'` étend cette confiance
    // aux scripts qu'ils chargent eux-mêmes (les morceaux de l'application), et rend inopérante
    // toute liste d'origines : un script hébergé ailleurs ne peut pas être injecté.
    ["script-src", nonceSource, "'strict-dynamic'", ...(isDevelopment ? ["'unsafe-eval'"] : [])],
    // En développement, Next.js injecte des styles en ligne sans nonce ; en production, les
    // feuilles de style viennent du site et les styles en ligne générés portent le nonce.
    ["style-src", "'self'", isDevelopment ? "'unsafe-inline'" : nonceSource],
    ["img-src", "'self'"],
    ["font-src", "'self'"],
    // Appels réseau : le site lui-même (navigation de Next.js) et l'API, rien d'autre. Une
    // donnée volée ne peut pas être envoyée vers un serveur tiers par `fetch`.
    ["connect-src", "'self'", apiOrigin],
    ["object-src", "'none'"],
    // Une balise `<base>` injectée pourrait rediriger tous les chemins relatifs.
    ["base-uri", "'none'"],
    ["form-action", "'self'"],
    // Interdit l'affichage dans un cadre : protection contre le détournement de clic.
    ["frame-ancestors", "'none'"],
    // En production, toute ressource demandée en HTTP est récupérée en HTTPS.
    ...(isDevelopment ? [] : [["upgrade-insecure-requests"]]),
  ];

  return directives.map((directive) => directive.join(" ")).join("; ");
}
