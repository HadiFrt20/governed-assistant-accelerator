# Sécurité et gouvernance

## Identité

- L'app est une Databricks App : accès par le SSO de l'espace de travail.
- Avec l'OBO activé, les appels gateway, Genie One et SQL portent l'identité de l'utilisateur. Genie One n'interroge que les tables auxquelles l'utilisateur a accès dans Unity Catalog. La gateway attribue chaque appel à l'utilisateur.
- Scopes demandés par l'app : `ai-gateway`, `sql`, `dashboards.genie`, `files`, `serving.serving-endpoints`.
- Échec fermé : si le jeton utilisateur est refusé (scopes pas encore acceptés), la demande s'arrête et l'utilisateur est invité à se reconnecter. L'app ne répond jamais avec les droits de son principal de service, sauf si `security.obo_fallback_to_app: true` est explicitement configuré ; dans ce cas l'écran l'affiche et la trace porte `obo_refused`.
- Identité : l'app lit `x-forwarded-email` posé par le proxy Databricks Apps ; sans cet en-tête, elle répond 401 (sauf en développement local, hors Apps).

## Les données restent dans Databricks

- Les modèles sont servis par Databricks via Unity AI Gateway ; aucune clé de fournisseur n'est stockée dans l'app.
- Les pièces jointes et les livrables sont dans un volume Unity Catalog ; l'historique dans Lakebase ; les traces dans des tables Unity Catalog.
- Une demande bloquée par une politique s'arrête dans la gateway : elle n'atteint pas le modèle et ne consomme pas de tokens.

## Politiques Unity AI Gateway

| Politique | Type | Effet |
|---|---|---|
| Anti-fuite PI | Fonction UC personnalisée (`CONTAINS(LOWER(...))` sur une liste de mots-clés) | DENY avec une raison métier |
| Anti-jailbreak | Intégrée Databricks, pre_call | DENY |
| Contenu inapproprié | Intégrée Databricks, pre_call et post_call | DENY |
| Données sensibles | Intégrée Databricks, pre_call et post_call, action `transform` | Anonymisation : e-mail, téléphone, IBAN, carte remplacés par des jetons |

Points d'attention :

- Les politiques SQL personnalisées sont transpilées en CEL : pas de `COALESCE`, pas d'expressions régulières ; chaque branche doit renvoyer une raison.
- Une politique non évaluable (catégorie invalide, erreur transitoire) **refuse la requête**. L'app réessaie deux fois une erreur « could not be evaluated ».
- L'action ASK n'est disponible que sur les services MCP.
- Les catégories actuelles ne couvrent ni les noms de personnes ni les identifiants français.

## Fichiers : qui peut lire et écrire quoi

Tous les accès au volume passent par le principal de service : l'app applique donc elle-même le cloisonnement (`app/security.py`).

- Chemins : chaque chemin est normalisé et doit rester sous une racine autorisée (pas de `..`, `?`, `#`, `%`, `\`).
- Pièces jointes : uniquement les fichiers de l'utilisateur, dans `uploads/<e-mail normalisé>/` ; le chemin envoyé par le navigateur est revalidé.
- `read_document` (outil du modèle) : bibliothèque ou dossier de l'utilisateur, jamais celui d'un autre.
- Téléversement : nom ramené à un nom de base ASCII, extensions autorisées (`docx pptx pdf txt csv md xlsx`), 25 Mo maximum, jamais d'écrasement.
- Bibliothèque partagée : contexte de confiance pour tous, donc écriture réservée aux `security.library_curators` (par défaut l'utilisateur qui installe), sans écrasement.

## Contenu non fiable (injection de consignes)

- Les documents sont lus par l'app et passés au modèle entre balises `<document>` neutralisées (une balise de fermeture dans le texte ne peut pas sortir du bloc). La consigne système précise que leur contenu est une donnée, jamais une consigne.
- Une demande bloquée par une politique est retirée de l'historique : elle ne repart pas avec les demandes suivantes.
- Limites : 20 000 caractères par message, 5 pièces jointes, 200 000 caractères de documents par demande, 2 demandes en cours par utilisateur, 1 par conversation.

## Interface

- Les réponses du modèle et de Genie One sont rendues en Markdown puis nettoyées par DOMPurify (bibliothèques livrées avec l'app, versions figées, aucun CDN à l'exécution).
- En-têtes : `Content-Security-Policy` (scripts de l'app uniquement, connexions vers l'app uniquement), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`.
- Les écritures (POST, PATCH, PUT, DELETE) venant d'une autre origine sont refusées.
- Transparence (AI Act, art. 50) : l'interface indique en permanence qu'il s'agit d'un assistant d'IA et chaque réponse porte « Généré par IA » ; les documents Office portent un marquage dans leurs propriétés (`keywords = contenu-genere-par-ia`, commentaire) et un pied de page ; les pages web portent `data-ai-generated` et une balise `meta`.

## Livrables et pages web

- Un fichier généré n'est téléchargeable que par l'utilisateur qui l'a produit (contrôle du propriétaire dans Lakebase avant lecture du volume).
- Les noms de fichiers sont ramenés en ASCII.
- Les pages HTML s'exécutent dans une origine isolée (`sandbox allow-scripts …`) et **sans accès réseau** : `default-src 'none'; connect-src 'none'; img-src data:; form-action 'none'; base-uri 'none'` (seules les polices de la marque sont autorisées). Le script écrit par le modèle ne peut ni appeler l'API de l'app ni envoyer de données à l'extérieur.
- `body_html` est réduit à de la mise en page : CSS, scripts, iframes, formulaires, gestionnaires d'événements, liens et sources externes sont retirés. Les données (`DATA`) sont échappées pour ne jamais pouvoir fermer la balise `<script>`.

## Ce qui est journalisé, et où

| Donnée | Emplacement |
|---|---|
| Appels modèle et MCP (utilisateur, service, tokens, latence, tags) | `system.ai_gateway.usage` (ingestion avec environ 1 h de délai) |
| Requêtes et réponses du model service | tables d'inférence `<service>_payload` |
| Conversations, tours, événements, fichiers | Lakebase, schéma `assistant` |
| Traces détaillées (entrées et sorties de chaque étape, données publiées, coût) | `<catalog>.<schema>.assistant_otel_spans` et tables voisines |
| Blocages par politique | événements de tour dans Lakebase, vue `gold_policy_decisions` |

La couche gold classe les demandes avec `ai_classify` (AI Functions, modèle hébergé par Databricks dans l'espace de travail). Ce chemin ne passe pas par les politiques du model service : les demandes bloquées n'y sont donc jamais envoyées (elles sont étiquetées directement). Pour une exigence plus stricte, désactivez le job d'usage ou classez sur un texte anonymisé.

Les décisions des politiques n'apparaissent pas dans `system.ai_gateway.usage` ni dans les tables d'inférence (constaté) : les blocages sont reconstitués à partir de ce que l'app reçoit.

Par défaut (`security.trace_content: redacted`), les traces gardent le début des textes longs (documents, résultats d'outils, raisonnement) avec leur taille et une empreinte SHA-256, pas leur contenu intégral ; les journaux de l'app ne contiennent pas le contenu des documents. `trace_content: full` conserve tout (utile en recette, à réserver à un schéma à accès restreint). Les requêtes restent visibles dans les tables d'inférence et le tableau de bord : restreignez l'accès au schéma.

## Bibliothèques : les droits Unity Catalog, rien de plus

Avec l'OBO, l'application liste, lit et ajoute les documents des bibliothèques avec le jeton de l'utilisateur (scope `files`, Files API). Unity Catalog décide : `READ VOLUME` pour lire une bibliothèque, `WRITE VOLUME` pour y ajouter. Un refus est affiché comme tel et n'est jamais retenté avec l'identité de l'application. La recherche ne renvoie que des passages des bibliothèques que l'utilisateur peut lire (contrôlé par son jeton). L'index de recherche est construit par l'application, qui a `READ VOLUME` sur les bibliothèques et rien d'autre.

Les fichiers personnels (pièces jointes, livrables) restent dans le volume des fichiers, servis par l'application et confinés par utilisateur : les droits Unity Catalog s'appliquent à un volume entier, pas à un dossier, et donner aux utilisateurs l'accès direct à ce volume leur ouvrirait les fichiers des autres.

## Droits accordés au déploiement

Déclarés dans le bundle (`bundle/resources/*.yml`), donc relus et réappliqués à chaque déploiement ; un test vérifie qu'aucun autre droit n'est donné à l'app. Principal de service de l'application, au plus juste :

| Objet | Droits | Où |
|---|---|---|
| Catalogue | `USE CATALOG` | `install/install.py`, phase `govern` (le catalogue existe avant le bundle) |
| Schéma `<schema>` | `USE SCHEMA` (pas de `SELECT`/`MODIFY` au niveau du schéma) | bundle |
| Schéma `<traces_schema>` | `USE SCHEMA`, `SELECT`, `MODIFY` : seul endroit où l'app écrit des tables (spans MLflow) | bundle |
| Volume `<volume>` (fichiers personnels) | `READ VOLUME`, `WRITE VOLUME` | bundle |
| Volumes des bibliothèques | `READ VOLUME` (indexation) | bundle (`resources/libraries.yml`, généré depuis `libraries`) |
| Service de modèles de l'assistant | `EXECUTE` (appels quand l'OBO est désactivé ou en secours) | bundle |
| Fonction de la politique personnalisée | `EXECUTE` | phase `govern` |
| Expérience MLflow, warehouse, base Lakebase | `CAN_MANAGE`, `CAN_USE`, `CAN_CONNECT_AND_CREATE` | ressources de l'app dans le bundle (accordées par Databricks Apps) |

Groupe `access.users_group` (par défaut `account users`) : `CAN_USE` sur l'app, `USE CATALOG` et `USE SCHEMA`, `EXECUTE` sur les services de modèles et sur la fonction de politique (avec l'OBO, les appels sont faits avec l'identité de l'utilisateur). En production, utilisez un groupe dédié plutôt que `account users`.

Limite connue : les privilèges Unity Catalog sur un volume s'appliquent au volume entier, pas à un sous-dossier. Le principal de
service peut donc techniquement écrire aussi dans `library/`. Pour séparer strictement, créez deux volumes (bibliothèque en lecture
seule pour l'application, livrables en écriture) ; l'application contrôle déjà que seuls les `library_curators` publient dans la bibliothèque.

Les utilisateurs n'ont besoin d'aucun droit sur le volume : l'application lit et sert les fichiers.
