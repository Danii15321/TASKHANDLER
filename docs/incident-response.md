# Plan de réponse à incident

But : limiter les dégâts, préserver les preuves, rétablir le service, informer qui doit l'être.
Inspiré du NIST SP 800-61 (préparer, détecter, contenir, éradiquer, rétablir, tirer les leçons).

## Rôles et contacts

| Rôle | Qui | Responsabilité |
|---|---|---|
| Responsable de l'incident | Le mainteneur du dépôt | Décide, coordonne, tient le journal |
| Délégué à la protection des données | À désigner avant la mise en ligne | Notification CNIL, information des personnes |

Tenir un **journal d'incident** horodaté (UTC) dès la détection : faits, décisions, commandes.

## 1. Détecter

Signaux à surveiller dans les journaux JSON (`docker compose -f compose.prod.yaml logs`) :

| Signal | Où | Seuil d'alerte |
|---|---|---|
| `refresh_token_reuse` | API, journal d'audit | Dès la première occurrence : vol de jeton probable |
| `login_failed`, `rate_limited` | API, journal d'audit | Hausse soudaine : force brute ou bourrage d'identifiants |
| `unhandled_exception` (500) | API | Toute série : défaut ou tentative d'exploitation |
| `backup_failed` | `backup-scheduler` | Dès la première occurrence |
| `pg_hba.conf rejects`, `password authentication failed` | PostgreSQL | Toute occurrence : accès interne anormal |
| Alerte de dépendance ou d'image | CI (`audit`, `image`) | Vulnérabilité haute ou critique |

## 2. Qualifier

| Gravité | Exemple | Délai d'action |
|---|---|---|
| Critique | Fuite de données personnelles, secret exposé, accès non autorisé à la base | Immédiat |
| Haute | Vulnérabilité exploitable publiée, compte compromis | Dans la journée |
| Moyenne | Attaque bloquée par les limites, indisponibilité partielle | Sous 72 h |

## 3. Contenir (sans détruire les preuves)

1. **Copier les journaux** avant tout redémarrage :
   `docker compose -f compose.prod.yaml logs --no-color > incident-<date>.log` (fichier hors du
   dépôt, accès restreint : il peut contenir des adresses IP).
2. **Sauvegarde immédiate** : `docker compose -f compose.prod.yaml run --rm backup`.
3. Selon le cas :
   - compte compromis : révoquer toutes ses sessions (incrémenter `token_version`, révoquer ses
     jetons de renouvellement) ;
   - secret exposé : rotation immédiate (voir `docs/exploitation.md`) ; la clé JWT en premier ;
   - attaque en cours : couper l'accès public (`docker compose ... stop nginx`), l'API et la base
     restent intactes pour l'analyse.

## 4. Éradiquer et rétablir

- Corriger la cause : **commencer par un test qui reproduit le défaut** (règle du projet), puis
  corriger, puis CI verte, puis pull request vers `main`.
- Secrets : renouveler **tous** ceux qu'un attaquant a pu lire, pas seulement celui qui a fui.
- Données altérées : restaurer la dernière sauvegarde saine dans une pile isolée
  (`scripts/restore-check.sh`, `KEEP=1`), comparer, puis basculer.
- Remettre nginx en service ; surveiller de près les signaux de la section 1 pendant 7 jours.

## 5. Notifier (RGPD, articles 33 et 34)

- **CNIL** : dans les **72 heures** après la prise de connaissance d'une violation de données
  personnelles, sauf si elle n'engendre pas de risque pour les personnes. Notifier même
  incomplètement, puis compléter.
- **Personnes concernées** : sans délai si le risque est élevé (par exemple, fuite d'adresses et
  de descriptions de tâches). Message clair : ce qui s'est passé, les données touchées, les
  mesures prises, ce qu'elles doivent faire (changer leur mot de passe).
- Consigner toute violation dans le registre, même non notifiée.

## 6. Tirer les leçons

Sous deux semaines : chronologie, cause racine, ce qui a fonctionné, ce qui a manqué. Chaque
action corrective devient une tâche de la roadmap, avec son test.

## Préparation (à vérifier régulièrement)

- Test de restauration au moins une fois par mois (`scripts/restore-check.sh`), durée notée.
- Clé privée de sauvegarde et clé de l'autorité hors du serveur, accessibles en cas de besoin.
- Ce document relu à chaque phase qui modifie l'architecture.
