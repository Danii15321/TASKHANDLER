#!/bin/sh
# Démarre Redis en production : TLS seul (aucun port en clair), un seul utilisateur aux droits
# limités, mot de passe lu dans le fichier secret.
set -eu

umask 077

# Redis accepte l'empreinte SHA-256 du mot de passe (`#…`) : le mot de passe en clair n'apparaît
# ni dans la configuration, ni dans la ligne de commande visible par `ps`.
password_hash="$(tr -d '\n' < /run/secrets/redis_password | sha256sum | cut -d ' ' -f 1)"

cat >/tmp/redis.conf <<EOF
# Port en clair fermé : seul le port TLS écoute.
port 0
tls-port 6380
tls-cert-file /run/tls/redis.crt
tls-key-file /run/tls/redis.key
tls-ca-cert-file /run/tls/ca.crt
# Pas de certificat client : l'authentification se fait par mot de passe, à travers TLS.
tls-auth-clients no
tls-protocols "TLSv1.2 TLSv1.3"

# Les compteurs de limitation sont éphémères : aucune écriture sur disque.
save ""
appendonly no

user default off
# Mêmes droits qu'en développement : clés \`rl:*\` et commandes du limiteur seulement.
user taskhandler_app on #${password_hash} resetkeys ~rl:* resetchannels -@all +ping +time +evalsha +script|load +zadd +zcard +zrange +zremrangebyscore +pexpire +hget +hset +hincrby +del
EOF

exec redis-server /tmp/redis.conf
