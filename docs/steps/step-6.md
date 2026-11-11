# Phase 6 — Protection contre les abus

## Objectif

Rendre les attaques automatisées coûteuses, sans permettre de bloquer un compte légitime.

**Résultat attendu :** un test de force brute est bloqué sans verrouiller durablement la victime.

Le code complet et commenté se trouve dans `backend/`. Ce guide en explique les décisions.

## Étape 1 — Redis, avec le moindre privilège

```bash
uv add redis
```

Pourquoi Redis ? Les compteurs doivent être **partagés** par toutes les instances de l'API : un
compteur en mémoire se remettrait à zéro à chaque redémarrage, et chaque instance aurait le sien.

Dans `compose.yaml`, Redis est publié sur `127.0.0.1` seulement, tourne sous l'utilisateur
`redis` et ne garde rien sur disque. `docker/redis/start-redis.sh` écrit sa configuration :

```text
user default off
user taskhandler_app on #<sha256> resetkeys ~rl:* resetchannels -@all +ping +time +evalsha ...
```

- l'utilisateur par défaut, sans mot de passe historiquement, est **désactivé** ;
- l'application n'accède qu'aux clés `rl:*`, et seulement aux commandes du limiteur : ni
  `FLUSHALL`, ni `CONFIG`, ni `KEYS` ;
- le mot de passe n'apparaît que sous forme d'empreinte, jamais dans la ligne de commande.

Licence : Redis 8 est publié sous AGPLv3 (ou licences propriétaires). L'utiliser sans le modifier,
comme ici, n'impose rien au code de TaskHandler. Valkey, fork sous licence BSD, est compatible.

## Étape 2 — Des compteurs atomiques (`app/core/rate_limit.py`)

Lire un compteur, le comparer, puis l'écrire en trois commandes laisse une fenêtre : 20 requêtes
simultanées liraient toutes « 4 » et passeraient toutes. Chaque opération est donc un **script
Lua**, que Redis exécute d'un bloc.

**Fenêtre glissante.** Une entrée datée par requête acceptée, dans un ensemble trié :

```lua
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[3])
  return {1, 0}
end
```

Une requête refusée n'est **pas** comptée : la limite se libère d'elle-même à la fin de la fenêtre.
L'heure vient de Redis (`TIME`) : toutes les instances partagent la même horloge.

**Clés hachées.** `rl:login-account:<sha256(ip|adresse)>` : ni IP ni adresse en clair dans Redis.

**Réponses vérifiées.** Une réponse de Redis est une donnée externe (OWASP API10) : un type ou une
valeur inattendus donnent une erreur, jamais une décision.

## Étape 3 — Les limites

| Route | Limite | Clé |
|---|---|---|
| Connexion | 20/min, et 5/min | IP, et couple IP + compte |
| Inscription | 5/h, et 3/h | IP, et adresse |
| Renouvellement | 30/min | Utilisateur |
| Routes authentifiées | 120/min | Utilisateur, déclarée sur le routeur |

Toutes sont réglables, mais **bornées** dans `Settings` : une erreur de réglage ne peut pas les
supprimer. Le dépassement répond `429` au format RFC 9457, avec `Retry-After` en secondes.

Les routes métier déclarent `AUTHENTICATED_DEPENDENCIES` : authentification **puis** limite, en
une ligne par routeur.

## Étape 4 — La temporisation progressive

Par couple IP + compte : 5 échecs gratuits, puis 1 s, 2 s, 4 s… **plafonné à 15 minutes**. Une
réussite efface tout, et le compteur expire après 24 heures sans échec.

> **Sécurité : pourquoi jamais de verrouillage définitif ?** Un compte verrouillé après N échecs
> permet à n'importe qui de bloquer la victime à distance : il suffit d'échouer N fois avec son
> adresse. Ici, la clé inclut l'IP : l'attaquant se ralentit lui-même, et la victime, depuis son
> IP, n'est jamais gênée. Une attaque répartie sur de nombreuses IP reste ralentie IP par IP.

Un compte inconnu est compté comme un compte existant : la temporisation ne révèle rien.

## Étape 5 — Redis indisponible

| Route | Comportement | Raison |
|---|---|---|
| Connexion, inscription, renouvellement | `503`, refus | Sans compteur, la force brute serait libre |
| Routes authentifiées, dont `logout` | Requête acceptée, avertissement journalisé | Une panne ne doit pas empêcher de couper une session volée |
| `/health/ready` | `503` | Le service n'est pas en état d'authentifier |

Les délais Redis sont courts (0,5 s) et sans nouvel essai : une panne donne une réponse **rapide**.

## Étape 6 — L'IP du client (`app/core/client_ip.py`)

`X-Forwarded-For` est écrit par le client lui-même : sans précaution, un attaquant change d'IP à
chaque essai. Règle appliquée :

1. si la connexion ne vient pas d'un proxy listé dans `TASKHANDLER_TRUSTED_PROXIES` (vide par
   défaut), l'en-tête est **ignoré** ;
2. sinon, l'en-tête est lu **de droite à gauche**, et la première adresse qui n'est pas un proxy
   de confiance est celle du client ; les entrées plus à gauche ont pu être inventées.

> **Piège constaté.** Par défaut, Uvicorn fait confiance à `X-Forwarded-For` venant de
> `127.0.0.1`, et réécrit l'IP **avant** notre code. Sur le vrai serveur, 8 essais avec un en-tête
> usurpé passaient tous ; avec `--no-proxy-headers`, le 6ᵉ est refusé. Lancez toujours :

```bash
uv run --locked uvicorn app.main:create_app --factory --no-server-header --no-proxy-headers
```

## Étape 7 — La taille des corps (`app/core/body_limit.py`)

Un middleware ASGI refuse en `413` :

- un `Content-Length` supérieur à 1 Mio, sans rien lire ;
- un corps envoyé par morceaux, sans taille annoncée : les octets sont comptés pendant la lecture.

Un `Content-Length` illisible est refusé en `400`.

## Étape 8 — Tester

Redis réel, avec un préfixe de clés unique par test : les compteurs ne se croisent jamais.

- force brute : 6ᵉ essai refusé, même avec le bon mot de passe ; la victime, depuis son IP, passe ;
- temporisation : 1 s puis 2 s, libérée d'elle-même, effacée par une réussite, plafonnée ;
- 20 requêtes simultanées : exactement 5 acceptées (compteur atomique) ;
- `X-Forwarded-For` usurpé sans proxy de confiance : sans effet ; avec proxy : clients séparés ;
- droits Redis : `FLUSHALL`, `CONFIG`, `KEYS`, `ACL` et clés hors préfixe refusés ;
- Redis injoignable : `503` en moins de 2 s, sauf pour la déconnexion ;
- corps trop grand, annoncé ou envoyé par morceaux : `413`.

## À retenir

- Ralentir l'attaquant ne doit jamais bloquer la victime.
- Un en-tête HTTP n'est fiable que s'il a été écrit par **votre** infrastructure.
- Sur une route d'authentification, sans moyen de compter, on refuse.

**Normes concernées :** OWASP API Security Top 10 2023, API2, API4, API6 et API10 ; OWASP ASVS,
chapitre V2.2 (protection contre la force brute) ; RFC 6585 (`429`) ; RFC 7239 (`Forwarded`).
