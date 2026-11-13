# Image de production de l'API TaskHandler, construite depuis `backend/` :
#   docker build -t taskhandler-api .
# Chaque image de base est épinglée par empreinte : un tag peut être republié, pas une empreinte.
# La mettre à jour est un changement relu, comme une dépendance Python.

# --- Étape 1 : construction de l'environnement Python ------------------------------------------
# Python 3.14.10 sur Debian 13 (trixie), même version que `.python-version`.
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build

# uv 0.13.7, copié depuis son image officielle : seul le binaire est repris, pas l'image.
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv

# - pas de téléchargement d'interpréteur : celui de l'image, épinglé, est le seul utilisé ;
# - bytecode compilé ici : le conteneur final tourne en lecture seule ;
# - copie des fichiers plutôt que liens vers un cache absent de l'image finale.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Les dépendances d'abord, seules : tant que `uv.lock` ne change pas, cette couche reste en cache.
COPY pyproject.toml uv.lock .python-version ./
# `--locked` : échec si le verrou ne correspond plus à `pyproject.toml` ; `--no-dev` : ni outils de
# test ni outils d'analyse dans l'image de production.
RUN uv sync --locked --no-dev --no-install-project

# --- Étape 2 : image d'exécution -----------------------------------------------------------------
# Même image de base, sans uv ni cache : seuls l'environnement et le code sont recopiés.
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS runtime

# Utilisateur dédié, sans mot de passe, sans shell de connexion et sans dossier personnel.
# UID fixe et élevé : il ne correspond à aucun compte de la machine hôte.
# Correctifs de sécurité Debian publiés depuis la construction de l'image de base : ils sont
# appliqués à chaque construction, sans attendre une nouvelle image officielle. La base reste
# épinglée ; l'analyse de la CI contrôle le résultat. Les listes de paquets sont supprimées.
# `pip` de l'image de base est retiré : rien ne s'installe à l'exécution, et un outil absent ne
# peut ni servir à un attaquant ni porter de vulnérabilité.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 taskhandler \
    && useradd --system --uid 10001 --gid taskhandler --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin taskhandler \
    && rm -rf /usr/local/bin/pip* /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info

WORKDIR /app
# Fichiers possédés par root et seulement lisibles par l'application : un processus compromis ne
# peut pas réécrire son propre code.
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

ENV PATH=/app/.venv/bin:$PATH \
    # Aucun fichier `.pyc` écrit à l'exécution : le système de fichiers est en lecture seule.
    PYTHONDONTWRITEBYTECODE=1 \
    # Journaux transmis immédiatement à Docker, sans tampon qui les perdrait en cas d'arrêt brutal.
    PYTHONUNBUFFERED=1

USER taskhandler:taskhandler
EXPOSE 8000

# Sonde sans dépendance ajoutée (pas de curl dans l'image) ; `127.0.0.1` doit figurer dans
# `TASKHANDLER_ALLOWED_HOSTS`.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

# - `--no-proxy-headers` : l'adresse du client vient de `TASKHANDLER_TRUSTED_PROXIES`, jamais d'un
#   en-tête accepté sans contrôle ;
# - `--no-server-header` : la version du serveur n'est pas annoncée.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header", "--timeout-graceful-shutdown", "10"]
