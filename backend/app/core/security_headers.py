"""En-têtes de sécurité ajoutés à chaque réponse de l'API."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Une API JSON n'a rien à charger, ni à afficher dans un cadre : tout est interdit par défaut.
_BASE_HEADERS = {
    # Le navigateur ne devine jamais le type d'un contenu : un JSON ne peut pas devenir un script.
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Aucune réponse de l'API ne doit être gardée par un cache : elles contiennent des données
    # personnelles ou des jetons, et un cache partagé les servirait à quelqu'un d'autre.
    "Cache-Control": "no-store",
    # Une autre origine ne peut pas inclure nos réponses (balise `<script>`, `<img>`) pour les
    # lire par un canal auxiliaire (Spectre). Les appels CORS autorisés n'en sont pas affectés.
    "Cross-Origin-Resource-Policy": "same-origin",
}
# Deux ans, sous-domaines compris : un navigateur qui a vu ce domaine refusera le HTTP en clair.
_HSTS = "max-age=63072000; includeSubDomains"
# Pages de documentation, actives seulement en développement : elles chargent des scripts et
# des styles, que la politique ci-dessus bloquerait.
_DOCUMENTATION_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


class SecurityHeadersMiddleware:
    """Ajoute les en-têtes de sécurité à toutes les réponses, erreurs comprises."""

    # requires : `app` est l'application à envelopper.
    # assigns  : rien.
    # ensures  : le middleware ajoute HSTS si `enable_hsts`, et laisse la documentation intacte si
    #            `documentation_enabled`.
    def __init__(self, app: ASGIApp, *, enable_hsts: bool, documentation_enabled: bool) -> None:
        self._app = app
        self._enable_hsts = enable_hsts
        self._documentation_enabled = documentation_enabled

    # requires : les arguments respectent le protocole ASGI.
    # assigns  : complète les en-têtes de la réponse au moment de son envoi.
    # ensures  : chaque réponse HTTP porte les en-têtes de sécurité ; un en-tête déjà fixé par la
    #            route (par exemple `Cache-Control`) n'est jamais affaibli, seulement complété.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (
            self._documentation_enabled and scope["path"] in _DOCUMENTATION_PATHS
        ):
            await self._app(scope, receive, send)
            return

        # requires : `message` est un message ASGI de réponse.
        # assigns  : ajoute les en-têtes manquants au début de la réponse.
        # ensures  : transmet le message, complété, au serveur.
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if self._enable_hsts:
                    headers.setdefault("Strict-Transport-Security", _HSTS)
            await send(message)

        await self._app(scope, receive, send_with_headers)
