// Configuration publique du frontend.
//
// Toute variable `NEXT_PUBLIC_*` est recopiée telle quelle dans le JavaScript envoyé au
// navigateur, au moment du build : elle est donc publique par construction. Ce module en dresse
// la liste fermée, refuse toute autre variable de ce préfixe (un secret mal nommé finirait sinon
// dans le bundle) et valide chaque valeur avant que l'application ne démarre.

// Liste fermée des variables publiques autorisées. En ajouter une est un choix relu.
const ALLOWED_PUBLIC_VARIABLES: ReadonlySet<string> = new Set(["NEXT_PUBLIC_API_BASE_URL"]);

const PUBLIC_PREFIX = "NEXT_PUBLIC_";

// Configuration publique, une fois validée.
export interface PublicConfig {
  // Origine de l'API (schéma, hôte et port, sans chemin), par exemple
  // `https://api.taskhandler.localhost:8443`. Sert aux appels et à la directive `connect-src`.
  readonly apiOrigin: string;
}

// Valeurs brutes, telles que lues dans l'environnement.
export interface RawPublicConfig {
  readonly apiBaseUrl: string | undefined;
}

// Erreur de configuration : l'application refuse de démarrer.
export class PublicConfigError extends Error {
  // requires : `message` décrit le problème sans recopier la valeur fautive.
  // assigns  : rien.
  // ensures  : construit une erreur nommée `PublicConfigError`.
  constructor(message: string) {
    super(message);
    this.name = "PublicConfigError";
  }
}

// requires : `env` est l'environnement du processus (par exemple `process.env`).
// assigns  : rien.
// ensures  : réussit si chaque variable `NEXT_PUBLIC_*` présente est dans la liste fermée ;
//            lève `PublicConfigError` sinon, en nommant les variables mais jamais leur valeur.
export function assertOnlyAllowedPublicVariables(
  env: Readonly<Record<string, string | undefined>>,
): void {
  const unexpected = Object.keys(env)
    .filter((name) => name.startsWith(PUBLIC_PREFIX) && !ALLOWED_PUBLIC_VARIABLES.has(name))
    .sort();
  if (unexpected.length > 0) {
    // Seuls les noms sont cités : si l'une de ces variables contient un secret, il ne doit
    // apparaître ni dans la console ni dans les journaux de la CI.
    throw new PublicConfigError(
      `variables publiques non autorisées : ${unexpected.join(", ")}. ` +
        "Toute variable NEXT_PUBLIC_* est envoyée au navigateur.",
    );
  }
}

// requires : `raw` contient les valeurs lues dans l'environnement ; `allowInsecureHttp` n'est
//            vrai qu'en développement local.
// assigns  : rien.
// ensures  : renvoie la configuration validée ; lève `PublicConfigError` si l'adresse de l'API
//            manque, n'est pas une URL, n'est pas en HTTPS (hors développement), ou contient des
//            identifiants, un chemin, une requête ou un fragment.
export function parsePublicConfig(
  raw: RawPublicConfig,
  options: { readonly allowInsecureHttp: boolean },
): PublicConfig {
  const value = raw.apiBaseUrl?.trim();
  if (value === undefined || value === "") {
    throw new PublicConfigError("NEXT_PUBLIC_API_BASE_URL est obligatoire.");
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new PublicConfigError("NEXT_PUBLIC_API_BASE_URL n'est pas une URL valide.");
  }

  // HTTPS partout, sauf en développement local : un jeton envoyé en clair serait interceptable.
  const allowedProtocols = options.allowInsecureHttp ? ["https:", "http:"] : ["https:"];
  if (!allowedProtocols.includes(url.protocol)) {
    throw new PublicConfigError(
      options.allowInsecureHttp
        ? "NEXT_PUBLIC_API_BASE_URL doit utiliser http ou https."
        : "NEXT_PUBLIC_API_BASE_URL doit utiliser https hors développement.",
    );
  }

  // Des identifiants dans l'URL seraient publiés dans le bundle et envoyés à chaque requête.
  if (url.username !== "" || url.password !== "") {
    throw new PublicConfigError("NEXT_PUBLIC_API_BASE_URL ne doit pas contenir d'identifiants.");
  }

  // Une origine seule : le préfixe `/api/v1` est fixé dans le code du client, et la directive
  // CSP `connect-src` n'accepte qu'une origine propre.
  if (url.pathname !== "/" || url.search !== "" || url.hash !== "") {
    throw new PublicConfigError(
      "NEXT_PUBLIC_API_BASE_URL doit être une origine, sans chemin, requête ni fragment.",
    );
  }

  return { apiOrigin: url.origin };
}

// requires : rien.
// assigns  : rien.
// ensures  : renvoie la configuration publique validée de ce build ; lève `PublicConfigError`
//            si elle est invalide. HTTP n'est accepté que sous `next dev`.
export function loadPublicConfig(): PublicConfig {
  // Accès littéral `process.env.NEXT_PUBLIC_…` : Next.js remplace cette expression par sa valeur
  // au moment du build. Le proxy et le navigateur voient donc exactement la même adresse, même
  // si l'environnement change au démarrage du serveur.
  return parsePublicConfig(
    { apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL },
    { allowInsecureHttp: process.env.NODE_ENV === "development" },
  );
}
