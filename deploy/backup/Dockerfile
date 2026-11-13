# Image de sauvegarde : `pg_dump` de la même version que le serveur, et `age` pour chiffrer.
# Construite par `deploy/compose.prod.yaml` ; elle ne tourne qu'à la demande, jamais en continu.

# Même image que le serveur PostgreSQL : `pg_dump` 18.6, compatible avec le format du serveur.
FROM postgres:18-bookworm@sha256:f1ea2a1a4fb7b68d4836be1fdcb8a6466a89b072158bf9d97663600538c294c5

# `age` vient de l'archive Debian, signée, en version fixée : aucun binaire téléchargé à la main.
# `--no-install-recommends` et le nettoyage des listes gardent l'image minimale.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends age=1.1.1-1+b3 \
    && rm -rf /var/lib/apt/lists/* \
    # Dossier des sauvegardes, à l'utilisateur `postgres` : le volume nommé en hérite.
    && install -d -o postgres -g postgres -m 0700 /backups

COPY backup.sh /usr/local/bin/backup.sh

# Jamais root : `pg_dump` n'a besoin que du réseau et du mot de passe du rôle de sauvegarde.
USER postgres
ENTRYPOINT []
CMD ["/usr/local/bin/backup.sh"]
