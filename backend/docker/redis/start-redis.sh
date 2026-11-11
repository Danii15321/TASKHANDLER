#!/bin/sh
# Démarre Redis avec un seul utilisateur, aux droits limités au strict nécessaire.
# `set -eu` : la moindre erreur, ou une variable manquante, empêche le démarrage.
set -eu

: "${TASKHANDLER_REDIS_PASSWORD:?Secret manquant, voir scripts/generate-dev-env.sh}"

# Lecture réservée au propriétaire du fichier de configuration généré.
umask 077

# Redis accepte l'empreinte SHA-256 du mot de passe (`#…`) : le mot de passe en clair n'apparaît
# ni dans le fichier de configuration, ni dans la ligne de commande visible par `ps`.
password_hash="$(printf '%s' "$TASKHANDLER_REDIS_PASSWORD" | sha256sum | cut -d ' ' -f 1)"

cat >/tmp/redis.conf <<EOF
# Les compteurs de limitation sont éphémères : aucune écriture sur disque.
save ""
appendonly no

# L'utilisateur par défaut, sans mot de passe dans les anciennes configurations, est désactivé.
user default off

# Seul utilisateur actif : clés en \`rl:*\` uniquement, et seulement les commandes du limiteur.
# Interdits de fait : FLUSHALL, KEYS, CONFIG, DEBUG, et toute clé hors du préfixe.
user taskhandler_app on #${password_hash} resetkeys ~rl:* resetchannels -@all +ping +time +evalsha +script|load +zadd +zcard +zrange +zremrangebyscore +pexpire +hget +hset +hincrby +del
EOF

exec redis-server /tmp/redis.conf
