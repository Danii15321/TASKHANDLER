// Erreurs du client HTTP.
//
// Toute réponse en échec devient une `ApiError` d'une catégorie connue. L'interface n'affiche que
// le message générique de cette catégorie : jamais le détail brut renvoyé par l'API, ni un
// message d'exception, qui pourraient révéler un fonctionnement interne.

// Catégories d'erreur, indépendantes du texte renvoyé par l'API.
export type ApiErrorKind =
  | "bad_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "rate_limited"
  | "unavailable"
  | "network"
  | "timeout"
  | "invalid_response"
  | "unexpected";

// Messages affichés, un par catégorie, en français et sans détail technique.
const USER_MESSAGES: Readonly<Record<ApiErrorKind, string>> = {
  bad_request: "La demande n’a pas pu être traitée.",
  unauthorized: "Votre session a expiré. Reconnectez-vous.",
  forbidden: "Cette action n’est pas autorisée.",
  not_found: "Élément introuvable.",
  conflict: "Cette action entre en conflit avec des données existantes.",
  validation: "Certaines informations sont invalides.",
  rate_limited: "Trop de tentatives. Patientez avant de réessayer.",
  unavailable: "Le service est momentanément indisponible. Réessayez plus tard.",
  network: "Connexion impossible. Vérifiez votre accès à Internet.",
  timeout: "Le serveur met trop de temps à répondre. Réessayez.",
  invalid_response: "Réponse inattendue du serveur.",
  unexpected: "Une erreur inattendue est survenue.",
};

// Erreur levée par le client HTTP.
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  // Code HTTP, absent pour une erreur réseau ou un délai dépassé.
  readonly status: number | undefined;
  // Délai demandé par l'API (`Retry-After`), en secondes, pour une erreur 429 ou 503.
  readonly retryAfterSeconds: number | undefined;
  // Identifiant de requête de l'API, utile au support pour retrouver le journal correspondant.
  readonly requestId: string | undefined;

  // requires : `kind` décrit la catégorie de l'échec.
  // assigns  : rien.
  // ensures  : construit une erreur dont le message est le texte générique de la catégorie.
  constructor(
    kind: ApiErrorKind,
    details: {
      readonly status?: number;
      readonly retryAfterSeconds?: number;
      readonly requestId?: string;
    } = {},
  ) {
    super(USER_MESSAGES[kind]);
    this.name = "ApiError";
    this.kind = kind;
    this.status = details.status;
    this.retryAfterSeconds = details.retryAfterSeconds;
    this.requestId = details.requestId;
  }
}

// requires : `status` est un code HTTP d'échec (400 à 599).
// assigns  : rien.
// ensures  : renvoie la catégorie correspondant au code ; `unexpected` pour un code non prévu.
export function kindForStatus(status: number): ApiErrorKind {
  switch (status) {
    case 400:
      return "bad_request";
    case 401:
      return "unauthorized";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 413:
    case 422:
      return "validation";
    case 429:
      return "rate_limited";
    case 502:
    case 503:
    case 504:
      return "unavailable";
    default:
      return "unexpected";
  }
}

// requires : `value` est la valeur brute de l'en-tête `Retry-After`, ou `null`.
// assigns  : rien.
// ensures  : renvoie le délai en secondes s'il s'agit d'un entier de 0 à 86 400 ; `undefined`
//            sinon. L'API n'envoie que des secondes : une date HTTP est ignorée plutôt que crue.
export function parseRetryAfter(value: string | null): number | undefined {
  if (value === null || !/^\d{1,6}$/.test(value.trim())) {
    return undefined;
  }
  const seconds = Number(value.trim());
  return seconds <= 86_400 ? seconds : undefined;
}

// requires : `error` est une valeur levée, quelle qu'elle soit.
// assigns  : rien.
// ensures  : renvoie le message à afficher : celui de l'`ApiError`, ou le message générique pour
//            toute autre valeur, dont le texte n'est jamais montré.
export function userMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : USER_MESSAGES.unexpected;
}
