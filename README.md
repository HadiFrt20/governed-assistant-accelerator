# Governed Assistant Accelerator

> Un assistant interne « type GPT », aux couleurs du client, qui tourne entièrement dans Databricks et dont chaque appel passe par Unity AI Gateway.
> *English summary at the end of this file.*

## Ce que c'est

Une Databricks App prête à déployer chez un client, qui donne aux métiers :

- un **chat** en français avec historique, épingles et recherche plein texte ;
- des **réponses chiffrées sur les données de l'entreprise** via **Genie One** (MCP), avec les droits de l'utilisateur et un lien « Approfondir dans Genie One » ;
- la **lecture des documents joints** (Word, PowerPoint, PDF) par l'application elle-même ;
- la **production de livrables** : PowerPoint et Word au modèle du client (dont un rapport 8D), et **pages web interactives** (cartographies, tableaux de bord) dans un design system imposé.

Et à la DSI :

- **un seul point de contrôle** : tous les appels modèle passent par des *model services* Unity AI Gateway, avec politiques (anti-fuite PI par fonction UC, anti-jailbreak, contenu inapproprié, **anonymisation** des données sensibles), limites de débit et tables d'inférence ;
- **l'identité de l'utilisateur** (OBO) sur les appels modèle, Genie One et SQL ;
- **la traçabilité** : une trace MLflow par demande (lecture des documents, raisonnement du modèle, décision des politiques, appels d'outils, Genie One, génération, réponse finale, coût estimé), stockée dans des tables Unity Catalog ;
- **le pilotage** : un tableau de bord AI/BI (adoption, usages classés par `ai_classify`, coûts, blocages, latence).

## Architecture

```
Utilisateurs (SSO) ──► Databricks App (FastAPI + UI de marque, OBO)
                         │
                         ├─ Routeur : question de données → Genie One (réponse courte + lien)
                         │            sinon → agent (Claude) + outils
                         │
                         ├─ Unity AI Gateway ── politiques : anti-fuite PI (fonction UC), jailbreak,
                         │   (model services)    contenu, anonymisation ; limites ; tables d'inférence
                         │      ├─ Claude (assistant bureautique)
                         │      ├─ Genie One MCP ──► tables Unity Catalog (droits de l'utilisateur)
                         │      └─ modèle de code (agents de développement, optionnel)
                         │
                         ├─ Générateurs : PowerPoint / Word (modèles de marque), pages HTML (design system)
                         │      └─► volume Unity Catalog (bibliothèque, générés, téléversés)
                         ├─ Lakebase (Postgres) : conversations, épingles, recherche BM25
                         └─ MLflow Tracing ──► tables UC <catalog>.<schema>.assistant_otel_*

system.ai_gateway.usage + historique ──► job horaire (ai_classify) ──► tableau de bord AI/BI
```

Détails : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prérequis

- Un espace de travail Databricks avec : **Unity AI Gateway** (model services et politiques), **Genie One** et son serveur MCP `system.ai.genie_one_mcp`, **Lakebase Autoscaling** (avec Lakebase Search pour le BM25), **Databricks Apps**, **MLflow 3** (traces dans Unity Catalog).
- Un **catalogue** Unity Catalog existant où vous avez les droits de créer un schéma, un volume, des fonctions et des tables.
- Un **SQL warehouse**.
- La **CLI Databricks** configurée avec un profil (`databricks auth login -p <profil>`), et Python 3.11+.

## Installation

1. Copiez `config/assistant.yaml`, renseignez le profil, le warehouse, le catalogue, le nom de l'app et la marque.
2. Lancez :

```bash
# prérequis : Databricks CLI connecté (databricks auth login --profile <profil>) et uv (https://docs.astral.sh/uv/)
uv run --python 3.11 --with-requirements requirements-dev.txt python install/install.py --config config/assistant.yaml
```

Pour garder vos valeurs hors du dépôt, copiez la configuration en `config/<env>.local.yaml` (ignoré par git) et passez ce fichier à `--config`.

L'installateur est idempotent. Il crée le schéma et le volume, les model services et leurs politiques, le projet Lakebase, l'expérience MLflow liée aux tables de traces UC, génère les modèles Office de la marque, déploie l'app avec ses droits (scopes OBO), le job d'usage et le tableau de bord. Avant de déployer l'app, il exécute la suite de tests sur le build exact (`preflight`, bloquant), puis un test de santé. Les model services existants ne sont modifiés que s'ils portent le marqueur de l'installateur (ou avec `--force`).

3. Ouvrez l'app une première fois dans une **fenêtre privée** pour accepter les nouveaux scopes : un jeton de session émis avant l'ajout du scope `ai-gateway` est refusé (403) et l'app demande alors à l'utilisateur de se reconnecter (elle ne répond pas avec ses propres droits).

## Personnaliser pour un client (environ 10 minutes)

1. `cp -R brands/acme brands/<client>`
2. Éditez `brands/<client>/brand.yaml` : nom, couleurs, polices, services, modèles de démarrage, suggestions, consignes métier.
3. Remplacez les trois logos (`wordmark.png` sur fond clair, `wordmark_on_dark.png` sur fond foncé, `mark.png` carré).
4. Dans `config/assistant.yaml` : `brand: <client>`, le périmètre de données (`data.scope`) et les mots-clés de la politique anti-fuite.
5. Relancez l'installateur.

Les modèles PowerPoint et Word sont régénérés depuis la marque (`install/office_templates.py`). Guide complet : [docs/CUSTOMIZE.md](docs/CUSTOMIZE.md).

## Référence de configuration

### `config/assistant.yaml`

| Clé | Rôle |
|---|---|
| `workspace.profile` | Profil CLI utilisé par l'installateur |
| `workspace.warehouse_id` | Warehouse pour le SQL d'installation, la lecture des traces et le tableau de bord |
| `brand` | Dossier de marque sous `brands/` |
| `unity_catalog.catalog` / `schema` / `volume` | Emplacement de tous les objets créés ; le volume contient `bibliotheque/`, `generes/`, `televerses/` |
| `app.name` | Nom de la Databricks App |
| `app.obo` | Appels modèle, Genie One et SQL avec l'identité de l'utilisateur |
| `models.assistant.service` / `model` | Model service gouverné de l'assistant et modèle de destination |
| `models.developer.*` | Model service optionnel pour les agents de code (même gateway, mêmes politiques) |
| `models.max_output_tokens` / `effort` | Budget de sortie (64 000 recommandé pour les pages HTML) et effort de raisonnement |
| `governance.rate_limit_per_user_per_minute` | Limite de débit par utilisateur |
| `governance.ip_leak_policy.*` | Politique personnalisée (fonction UC) : DENY si un mot-clé apparaît dans la demande |
| `governance.builtin_policies.*` | Garde-fous Databricks : jailbreak, contenu inapproprié, données sensibles (action `transform` = anonymisation) |
| `data.genie_one` / `data.scope` | Activer Genie One ; description des tables métier donnée au modèle |
| `observability.mlflow_experiment` | Expérience MLflow liée aux tables de traces UC |
| `observability.usage_job_cron` | Planification du job d'usage (couche gold + `ai_classify`) |
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

**Install:** fill in `config/assistant.yaml`, then `uv run --python 3.11 --with-requirements requirements-dev.txt python install/install.py --config config/assistant.yaml` (keep real values in an untracked `config/<env>.local.yaml`).
**New customer:** copy `brands/acme`, edit `brand.yaml`, swap the three logos, set `brand:` in the config, rerun the installer.
**Known limits:** see the French section above (ASK only on MCP services; no person-name or French-ID redaction category; invalid redaction category fails closed for all traffic; large HTML pages take 3 to 6 minutes; list prices to validate).
