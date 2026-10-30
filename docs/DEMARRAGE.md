# Démarrer

Ce guide s'adresse à la personne qui installe l'assistant, sans connaissance préalable de Databricks. Comptez 30 minutes, dont 20 d'attente.

## Avant de commencer

| Il vous faut | Comment l'obtenir |
|---|---|
| Un espace Databricks et un compte qui peut y créer des objets | Votre administrateur Databricks, ou votre contact Databricks |
| Un ordinateur (Mac, Linux ou Windows avec WSL) avec un terminal | |
| `uv` (exécute l'installation, sans rien installer d'autre) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| La CLI Databricks | `brew install databricks` (ou la page *Install the Databricks CLI* de la documentation) |

Si l'un des deux outils manque, `./assistant` vous le dit et donne la commande.

## 1. Répondre aux questions

```bash
./assistant init
```

| Question | Exemple | Si vous ne savez pas |
|---|---|---|
| Nom de votre entreprise | Boulangeries Dupré | obligatoire |
| Nom de l'assistant | Mia | Entrée : « Assistant <entreprise> » |
| Couleur principale | #0E2A47 | Entrée : bleu foncé. Une couleur trop claire est assombrie pour rester lisible |
| Logo | ~/Images/logo.png | Entrée : un logo est dessiné à partir du nom |
| Adresse de l'espace Databricks | https://xxx.cloud.databricks.com | demandée seulement à la première connexion ; une page de connexion s'ouvre |
| Catalogue | main | choisissez dans la liste ; l'assistant y crée son propre espace, sans toucher au reste |

À la fin, deux éléments sont créés : la marque (`brands/<entreprise>/`) et la configuration (`config/<entreprise>.local.yaml`, une dizaine de lignes).

## 2. Installer

```bash
./assistant deploy config/<entreprise>.local.yaml
```

L'installation contrôle d'abord l'espace (et dit précisément quoi activer s'il manque quelque chose), installe, puis vérifie en conditions réelles : l'application répond, une fuite de document confidentiel et une tentative de détournement sont bloquées, une adresse e-mail est anonymisée, une vraie demande aboutit et est tracée. Si une étape échoue, le message dit pourquoi et quoi faire, puis comment reprendre là où elle s'est arrêtée.

## 3. Ouvrir l'assistant

L'adresse s'affiche à la fin (`app https://…databricksapps.com`). La première fois, ouvrez-la dans une fenêtre de navigation privée et acceptez les autorisations demandées : l'assistant agit avec vos droits, jamais avec plus.

## Ensuite

| Pour | Faire |
|---|---|
| Ajouter des documents que l'assistant pourra lire et citer | Menu **Bibliothèque** > **Ajouter un document** (réservé aux responsables de la bibliothèque, par défaut la personne qui a installé) |
| Donner accès à vos équipes | Par défaut tous les utilisateurs de l'espace ; pour un groupe précis, `access.users_group` dans la configuration |
| Créer une bibliothèque par service, chacun ne voyant que la sienne | `libraries` dans la configuration (voir [CUSTOMIZE.md](CUSTOMIZE.md)) |
| Appliquer votre design system (Figma, CSS) | `./assistant design import --brand <entreprise> <fichier>` puis `./assistant deploy …` |
| Vérifier que tout fonctionne, à tout moment | `./assistant test config/<entreprise>.local.yaml` (environ 30 minutes, 27 contrôles) |
| Tout retirer | `./assistant remove config/<entreprise>.local.yaml` |

## Qui administre quoi, et où

Il n'y a pas de console d'administration à part : tout est un objet Databricks, visible dans l'interface Databricks.

| Pour | Où | Redéploiement ? |
|---|---|---|
| Donner ou retirer l'accès à l'assistant ou à une bibliothèque | ajouter ou retirer la personne du **groupe** Databricks concerné (`access.users_group`, `readers` / `curators` d'une bibliothèque) | non |
| Ajouter des documents | menu Bibliothèque de l'assistant (responsables), ou le volume dans Catalog Explorer | non |
| Changer les garde-fous, mots-clés anti-fuite, limites, modèles, bibliothèques | `config/<entreprise>.local.yaml`, puis `./assistant deploy …` | oui |
| Changer couleurs, logos, textes, design system | `brands/<entreprise>/`, puis `./assistant deploy …` | oui |

À savoir : les garde-fous et les droits gérés par l'assistant sont réécrits à chaque déploiement depuis le fichier de configuration. Une modification faite à la main dans l'interface Databricks sur ces objets-là (politiques des model services, droits sur les volumes de l'assistant) est donc remise à la valeur du fichier au déploiement suivant : modifiez le fichier, pas l'interface.
