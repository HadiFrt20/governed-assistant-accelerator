# Governed Assistant Accelerator

> Un assistant interne « type GPT », aux couleurs du client, qui tourne entièrement dans Databricks et dont chaque appel passe par Unity AI Gateway.
> *English summary at the end of this file.*

## Démarrer en 3 étapes

Aucune compétence Databricks n'est nécessaire pour installer. Il faut un espace Databricks et un ordinateur avec un terminal.

```bash
./assistant init                              # 2 minutes : nom de l'entreprise, nom de l'assistant, couleur et logo (facultatifs)
./assistant deploy config/<entreprise>.local.yaml   # 15 à 25 minutes : installe tout, puis vérifie que tout fonctionne
```

3. Ouvrez l'adresse affichée à la fin. C'est prêt. Guide pas à pas : [docs/DEMARRAGE.md](docs/DEMARRAGE.md).

Les questions sont en français ; la connexion à Databricks, l'entrepôt SQL et l'emplacement sont détectés. Tout le reste a une valeur par défaut sûre : garde-fous actifs, anonymisation, traçabilité, tableau de bord. Rien n'est à configurer pour commencer.

## Ce que vos équipes obtiennent

- un **chat** en français avec historique, épingles et recherche ;
- des **réponses chiffrées sur les données de l'entreprise** (Genie One), avec les droits de chacun ;
- une **bibliothèque documentaire** : l'assistant cherche dans vos procédures et documents internes, les lit (texte, tableaux, figures) et **cite la page** ; chacun ne voit que les bibliothèques auxquelles il a droit ;
- des **livrables** prêts à diffuser : PowerPoint, Word, pages web interactives, **à vos couleurs** (votre design system peut être importé depuis Figma ou un fichier CSS).

## Ce que la DSI obtient

- **les droits Databricks partout** : la bibliothèque, les données et les modèles sont lus avec l'identité de l'utilisateur ; Unity Catalog décide, l'application n'ajoute aucun droit ;
- **un seul point de contrôle pour l'IA** : tous les appels modèle passent par Unity AI Gateway (anti-fuite, anti-jailbreak, contenu inapproprié, **anonymisation** des données personnelles, limites de débit, journal des requêtes) ;
- **la traçabilité** : une trace MLflow par demande, stockée dans Unity Catalog, coût compris ;
- **le pilotage** : un tableau de bord d'usage et de gouvernance ;
- **un déploiement sans surprise** : tout est décrit dans un Databricks Asset Bundle, vérifié avant et après, réversible (`./assistant remove`).

Seules des fonctions Databricks disponibles pour tous (GA) sont activées par défaut. Les fonctions en préversion (validation humaine des actions des agents, évaluation continue par des juges IA, recherche BM25 Lakebase) sont des options documentées : [docs/OPERATIONS.md](docs/OPERATIONS.md#options).

## Architecture

```
Utilisateurs (SSO) ──► Databricks App (FastAPI + UI de marque, identité de l'utilisateur)
                         │
                         ├─ Routeur : question de données → Genie One (réponse courte + lien)
                         │            sinon → agent (Claude) + outils
                         │
                         ├─ Unity AI Gateway ── politiques : anti-fuite (fonction UC), jailbreak,
                         │   (model services)    contenu, anonymisation ; limites ; tables d'inférence
                         │      ├─ Claude (assistant)        ├─ Genie One MCP ──► tables UC (droits de l'utilisateur)
                         │      └─ modèle de code (agents de développement, optionnel)
                         │
                         ├─ Bibliothèques = volumes UC, lus avec le jeton de l'utilisateur (Files API)
                         │      ├─ ai_parse_document (texte, tableaux, figures, pages) ── cache Lakebase
                         │      └─ index plein texte Lakebase ──► search_library (passages + page, filtrés par droits)
                         ├─ Générateurs : PowerPoint / Word (modèles de marque), pages HTML (design system + contrôle avant publication)
                         │      └─► volume UC des fichiers personnels (générés, téléversés), confinés par utilisateur
                         ├─ Lakebase (Postgres) : conversations, épingles, recherche
                         └─ MLflow Tracing ──► tables UC <catalog>.<schema>_traces.assistant_otel_*

system.ai_gateway.usage + historique ──► job horaire (ai_classify) ──► tableau de bord AI/BI
```

Détails : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prérequis

- Un espace de travail Databricks avec : **Unity AI Gateway** (model services et politiques), **Genie One** et son serveur MCP `system.ai.genie_one_mcp`, **Lakebase Autoscaling**, **Databricks Apps**, **MLflow 3** (traces dans Unity Catalog), les **AI Functions** (`ai_parse_document`, `ai_classify`). Le contrôle préalable (`./assistant check`) vérifie chacun et dit quoi activer s'il manque quelque chose.
- Un **catalogue** Unity Catalog existant où vous avez les droits de créer un schéma, un volume, des fonctions et des tables.
- Un **SQL warehouse**.
- La **CLI Databricks** configurée avec un profil (`databricks auth login -p <profil>`), et Python 3.11+.

## Installation

Toutes les ressources de l'espace de travail sont déclarées dans un **Databricks Asset Bundle** (`bundle/`) : schémas, volume, model services (routage, limites de débit, table d'inférence, droits), projet Lakebase et son catalogue UC, expérience MLflow liée aux tables de traces UC, app (ressources, scopes OBO, variables d'environnement, droits), job d'usage, tableau de bord. `install/install.py` rend le bundle pour une configuration et une marque, le valide, le déploie, applique ce qu'un bundle ne sait pas encore exprimer, puis prouve le résultat en conditions réelles.

1. Copiez `config/assistant.yaml` en `config/<env>.local.yaml` (ignoré par git) et renseignez le profil, le warehouse, le catalogue, le nom de l'app et la marque.
2. Contrôlez sans rien modifier, puis déployez :

```bash
# prérequis : CLI Databricks ≥ 1.20 connectée (databricks auth login -p <profil>) et uv (https://docs.astral.sh/uv/)
uv run --python 3.11 --with-requirements requirements-dev.txt python install/install.py --config config/<env>.local.yaml --plan
uv run --python 3.11 --with-requirements requirements-dev.txt python install/install.py --config config/<env>.local.yaml
```

| Phase | Ce qui est fait, et vérifié avant de passer à la suite |
|---|---|
| `checks` | CLI, authentification, warehouse, catalogue (droit de créer un schéma), modèles de fondation, Unity AI Gateway actif, Genie One joignable, Lakebase disponible, configuration complète (toutes les erreurs listées d'un coup) |
| `build` | rendu du bundle dans `build/<app>/` (app, SQL, tableau de bord, valeurs des variables), remplacement atomique, puis **suite de tests sur ce build exact** (bloquant) |
| `deploy` | `bundle validate --strict`, détection des objets existants portant les mêmes noms (avec la commande `bind` à lancer), `bundle plan` refusé s'il supprime ou recrée une ressource qui porte des données (historique, schémas, volume, traces) sauf `--allow-destructive`, puis `bundle deploy` |
| `govern` | politiques de garde-fous sur les model services (relues après écriture), fonction UC de la politique personnalisée, `USE CATALOG`, dossiers du volume, données de référence, publication du tableau de bord |
| `start` | `bundle run` de l'app (déploiement du code et démarrage) |
| `verify` | santé de l'app, blocage effectif (politique anti-fuite et anti-jailbreak), anonymisation d'un e-mail, une vraie demande de bout en bout, spans présents dans les tables de traces UC, exécution du job d'usage et couche gold non vide |

Recette complète après déploiement (27 contrôles en conditions réelles) : `tests/live/acceptance.py`, voir [docs/OPERATIONS.md](docs/OPERATIONS.md).

Un échec arrête le déploiement avec la phase, la cause, la correction et la commande de reprise (`--from <phase>`). Chaque commande et sa sortie sont dans `build/<app>.deploy.log` (jetons masqués), le bilan dans `build/<app>.deploy.json`. Les codes de sortie distinguent la phase (2 configuration ou contrôles, 3 build, 4 deploy, 5 govern, 6 start, 7 verify). Relancer est sans risque : toutes les étapes sont idempotentes. `--destroy --yes` supprime tout ce que le déploiement a créé.

Point d'attention vérifié : une mise à jour d'un model service par le bundle **efface ses politiques** (le schéma du bundle n'a pas encore de champ pour elles). La phase `govern` les réapplique aussitôt et les relit ; si elles ne tiennent pas, l'installateur retire `EXECUTE` sur les services (échec fermé) avant de s'arrêter.

3. Ouvrez l'app une première fois dans une **fenêtre privée** pour accepter les scopes : un jeton de session émis avant l'ajout du scope `ai-gateway` est refusé (403) et l'app demande alors à l'utilisateur de se reconnecter (elle ne répond pas avec ses propres droits).

## Personnaliser pour un client (environ 10 minutes)

1. `cp -R brands/acme brands/<client>`
2. Éditez `brands/<client>/brand.yaml` : nom, couleurs, polices, services, modèles de démarrage, suggestions, consignes métier.
3. Remplacez les trois logos (`wordmark.png` sur fond clair, `wordmark_on_dark.png` sur fond foncé, `mark.png` carré).
4. Dans `config/assistant.yaml` : `brand: <client>`, le périmètre de données (`data.scope`) et les mots-clés de la politique anti-fuite.
5. Relancez `install/install.py`.

Les modèles PowerPoint et Word sont régénérés depuis la marque (`install/office_templates.py`). Guide complet : [docs/CUSTOMIZE.md](docs/CUSTOMIZE.md).

## Référence de configuration

### `config/assistant.yaml`

| Clé | Rôle |
|---|---|
| `workspace.profile` | Profil CLI utilisé pour déployer |
| `workspace.warehouse_id` | Warehouse du SQL de déploiement, du job d'usage, de la lecture des traces et du tableau de bord |
| `brand` | Dossier de marque sous `brands/` |
| `unity_catalog.catalog` / `schema` / `volume` | Emplacement des objets créés ; le volume contient `library/`, `generated/`, `uploads/` |
| `unity_catalog.traces_schema` | Schéma des tables de traces MLflow (par défaut `<schema>_traces`), seul schéma où l'app écrit |
| `app.name` | Nom de la Databricks App |
| `app.obo` | Appels modèle, Genie One et SQL avec l'identité de l'utilisateur |
| `access.users_group` | Groupe autorisé à ouvrir l'app et à appeler les services (`EXECUTE`) ; par défaut `account users` |
| `models.assistant.service` / `model` | Model service gouverné de l'assistant et modèle de destination |
| `models.developer.*` | Model service optionnel pour les agents de code (même gateway, mêmes politiques) |
| `models.max_output_tokens` / `effort` | Budget de sortie (64 000 recommandé pour les pages HTML) et effort de raisonnement |
| `governance.rate_limit_per_user_per_minute` | Limite de débit par utilisateur |
| `governance.ip_leak_policy.*` | Politique personnalisée (fonction UC) : DENY si un mot-clé apparaît dans la demande |
| `governance.builtin_policies.*` | Garde-fous Databricks : jailbreak, contenu inapproprié, données sensibles (action `transform` = anonymisation) |
| `data.genie_one` / `data.scope` | Activer Genie One ; description des tables métier donnée au modèle |
| `observability.mlflow_experiment` | Expérience MLflow liée aux tables de traces UC |
| `observability.usage_job_cron` / `timezone` | Planification du job d'usage (couche gold + `ai_classify`) |
| `pricing` | Prix par million de tokens (entrée, sortie) pour le coût estimé de chaque appel |

### `brands/<marque>/brand.yaml`

| Clé | Rôle |
|---|---|
| `name`, `company`, `site`, `site_short`, `org_label`, `language` | Nom de l'assistant et libellés affichés |
| `description` | Présentation de l'assistant dans le prompt système |
| `colors.primary` / `primary_2` / `accent` / `secondary` | Couleurs de l'interface, des pages HTML et des modèles Office |
| `fonts.heading` / `body` / `office` | Polices web et police des fichiers Office |
| `logos.*` | Fichiers du dossier de marque |
| `departments` | Services proposés à l'utilisateur (étiquettes de requête dans la gateway et le tableau de bord) |
| `templates` | Cartes de démarrage (type, nom, description, prompt) |
| `suggestions` | Questions suggérées par service |
| `instructions` | Consignes métier ajoutées au prompt système |
| `footer` | Mention en bas de l'interface |

## Exploitation

- **Traces** : chaque réponse affiche un lien « Trace d'exécution (MLflow) ». Les spans sont aussi requêtables en SQL dans `<catalog>.<schema>.assistant_otel_spans`.
- **Coût** : chaque span LLM porte `input_tokens`, `output_tokens`, `model` et `cost_estimate_usd` ; la trace porte le total.
- **Politiques** : le span `politiques_ai_gateway` montre la décision (ALLOW, DENY, ANONYMISATION), les données sensibles repérées dans la demande (masquées) et les jetons d'anonymisation revenus du modèle.
- **Tableau de bord** : adoption, usages, coûts, blocages, technique (latence, tokens, modèles, outils MCP).
- **Déploiement** : passez toujours par le contrôle préalable ; il bloque le déploiement si un chemin de code casse.

Voir [docs/OPERATIONS.md](docs/OPERATIONS.md) et [docs/SECURITY.md](docs/SECURITY.md).

## Limites connues (constatées en test)

- L'action **ASK** des politiques n'existe que sur les services MCP ; sur un model service elle se comporte comme un DENY.
- `detect_sensitive_data` n'a pas de catégorie « nom de personne » ni d'identifiant français, et n'a pas masqué un mobile français dans nos tests (`06 12 34 56 78`). Les e-mails, IBAN et cartes sont bien anonymisés.
- Une catégorie invalide dans `detect_sensitive_data` rend la politique non évaluable : la gateway **refuse alors toutes les requêtes** (fail closed). Une erreur « could not be evaluated » peut aussi être transitoire : l'app réessaie deux fois.
- Les grosses pages HTML prennent 3 à 6 minutes à générer.
- Claude Code installé sur un poste Databricks ne peut pas viser un autre espace de travail (paramètres gérés) ; la démo développeur utilise Codex CLI.
- Les prix de `pricing` sont les prix catalogue Anthropic (octobre 2026) ; validez-les contre votre contrat Databricks.
- Genie One ne lit pas les documents pour l'assistant et n'écrit pas de fichiers : l'app lit les pièces jointes et produit les livrables elle-même.

---

## English summary

A brandable internal "GPT-style" assistant that runs entirely inside Databricks. Every model call goes through Unity AI Gateway model services with guardrails (custom UC-function IP-leak policy, jailbreak, unsafe content, sensitive-data **redaction**), per-user rate limits and inference tables. Calls run with the signed-in user's identity (OBO). Data questions go to Genie One (MCP) and come back as a short answer plus a link to continue in Genie One. Attachments are read by the app. The assistant produces branded PowerPoint and Word files (including an 8D report) and interactive HTML pages rendered by an enforced design system. History, pins and BM25 search live in Lakebase. Every request is one MLflow trace stored in Unity Catalog tables, with per-call token counts and estimated cost. An AI/BI dashboard tracks adoption, `ai_classify`-classified usage, cost, blocks and latency.

**Install:** every workspace resource is a Databricks Asset Bundle (`bundle/`). Copy `config/assistant.yaml` to an untracked `config/<env>.local.yaml`, fill it in, then `uv run --python 3.11 --with-requirements requirements-dev.txt python install/install.py --config config/<env>.local.yaml` (add `--plan` first to change nothing). Phases checks, build (test gate), deploy, govern (guardrail policies), start, verify (live guardrail and end-to-end checks); failures stop with cause, fix and a `--from` resume command.
**New customer:** copy `brands/acme`, edit `brand.yaml`, swap the three logos, set `brand:` in the config, rerun `install/install.py`.
**Known limits:** see the French section above (ASK only on MCP services; no person-name or French-ID redaction category; invalid redaction category fails closed for all traffic; large HTML pages take 3 to 6 minutes; list prices to validate).
