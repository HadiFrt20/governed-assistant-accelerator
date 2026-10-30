# Exploitation

## Déployer une modification

Toujours par `install/install.py` (jamais `databricks bundle deploy` à la main dans `build/` : les politiques des model services ne seraient pas réappliquées). Commencez par `--plan` : il affiche ce que le bundle va créer, modifier ou supprimer, sans rien changer.

La phase `build` exécute la suite pytest (`tests/test_*.py`, Python 3.11, dépendances figées) sur le build exact qui va être déployé. Un échec bloque le déploiement. En local : `tests/run_all.sh` exécute la suite pour chaque marque de `brands/`. Couvert : cloisonnement des chemins et des fichiers, droits de la bibliothèque, propriétaire des tours, CSP, origine des écritures, identité manquante, échec fermé de l'OBO, outils de l'agent, injection via documents, historique après blocage, reprise après coupure, décodage SSE, détection des données sensibles, prix, rendu Office et HTML, marquage IA, image de marque sans fuite d'une marque à l'autre, et le déploiement lui-même : validation de la configuration, variables du bundle, droits minimaux de l'app, refus d'un plan destructeur, détection des conflits de noms, échec fermé si les politiques ne tiennent pas.

La phase `verify` prouve le déploiement en conditions réelles : santé de l'app, blocage par la politique anti-fuite et anti-jailbreak, anonymisation d'un e-mail, une demande de bout en bout, spans dans les tables de traces UC, job d'usage exécuté et couche gold non vide.

### En cas d'échec

Le message donne la phase, la cause, la correction et la commande de reprise :

```
DEPLOYMENT STOPPED in phase « deploy »
  what : existing objects with the bundle's names
  cause: resources.schemas.assistant_schema: main.acme_assistant
  fix  : either rename them in the config, delete them, or adopt them into this bundle:
  (cd build/acme-assistant && databricks bundle deployment bind assistant_schema 'main.acme_assistant' -p DEFAULT --auto-approve)
  resume: install/install.py --config config/assistant.yaml --from deploy
```

| Cas | Comportement |
|---|---|
| Erreur passagère (429, 5xx, délai, politique « could not be evaluated ») | nouvelle tentative avec attente croissante, puis arrêt |
| Objet existant hors bundle (même nom) | arrêt avant tout changement, commande `bind` proposée |
| Plan qui supprime ou recrée l'historique, un schéma, le volume ou l'expérience | arrêt, sauf `--allow-destructive` |
| Politiques non relues à l'identique après écriture | `EXECUTE` retiré aux utilisateurs et à l'app sur les services (échec fermé), arrêt ; la reprise `--from govern` les rétablit |
| App qui ne démarre pas | état du calcul et du déploiement dans le message, puis `databricks apps logs <app>` |

Journal complet : `build/<app>.deploy.log`. Bilan : `build/<app>.deploy.json`.

Constaté avec la CLI 1.20 : un redéploiement sans changement affiche `update apps.assistant` et `update ...grants`. L'API des apps ne renvoie que les noms des variables d'environnement, pas leurs valeurs : la CLI voit une différence (ancienne valeur = nouvelle), et les droits qui référencent le principal de service de l'app suivent. Les valeurs réécrites sont identiques ; aucune ressource n'est recréée et les politiques restent en place (la phase `govern` le vérifie).

`--destroy --yes` supprime le projet Lakebase en suppression douce : il reste récupérable 7 jours (`purge_time`), et son identifiant n'est pas réutilisable pendant ce délai. Pour réinstaller tout de suite, changez `app.name`.

## Lire une trace

Chaque réponse affiche « Trace d'exécution (MLflow) ». Spans principaux :

| Span | Contenu |
|---|---|
| nom de la marque (racine, ex. `Atlas`) | Question, service, utilisateur, conversation, route, coût total |
| `lecture_documents` > `lecture: <fichier>` | Taille et extrait du texte lu |
| `routeur` / `llm.routeur` | Décision GENIE ou AGENT et méthode (règle ou modèle) |
| `llm.agent` | Modèle, tokens (entrée, sortie, cache, raisonnement), effort, budget, raison d'arrêt, coût estimé |
| `raisonnement_modele` | Texte de raisonnement renvoyé par le modèle, s'il y en a |
| `politiques_ai_gateway` | Décision (ALLOW, DENY, ANONYMISATION), données sensibles repérées dans la demande (masquées), jetons revenus du modèle |
| `decision_outils` | Outils choisis et leurs entrées (les gros champs sont résumés) |
| `genie_one` | Question, conversation, durée, SQL, réponse |
| `create_html` / `create_pptx` / `create_docx` | Données publiées, chemin UC, chiffres publiés |
| `reponse_finale` | Réponse, livrables, coût estimé, blocage, anonymisation |

Requête SQL sur les spans :

```sql
SELECT trace_id, name, (end_time_unix_nano - start_time_unix_nano) / 1e9 AS secondes,
       attributes:cost_estimate_usd::double AS cout, attributes:decision::string AS decision
FROM <catalog>.<traces_schema>.assistant_otel_spans
ORDER BY start_time_unix_nano DESC
```

Le span `politiques_ai_gateway` indique ce que l'app a **repéré** dans la demande et ce qui est **revenu** du modèle. Le masquage lui-même se fait dans la gateway, qui ne le rapporte pas : la preuve est le retour des jetons `[EMAIL_ADDRESS]`, `[IBAN_CODE]`.

## Coûts

- Chaque span LLM porte `cost_estimate_usd`, calculé avec les prix de `pricing` (lectures de cache à 0,1 x l'entrée).
- Le tableau de bord affiche les tokens et un coût estimé par service et par pilier.
- Genie One n'a pas de coût de modèle dans la trace ; son calcul SQL n'est pas compté.
- Une page HTML importante coûte de l'ordre de 50 000 à 80 000 tokens. Le budget de sortie démarre à 64 000 tokens pour éviter une tentative perdue ; si la sortie est quand même coupée, l'app relance une seule fois en effort bas avec une consigne de concision, sans l'écrire dans l'historique. Les erreurs passagères de la gateway (429, 5xx) sont réessayées avec attente (en-tête `Retry-After` respecté).

## Tableau de bord

Trois onglets : adoption et usages (`ai_classify` du type d'usage et du métier), IA gouvernée (trafic gateway, blocages, coûts), technique (latence, tokens, modèles, outils MCP, politiques). Les données d'usage gateway arrivent avec environ une heure de délai : générez du trafic une heure avant une démonstration.

## Incidents connus

| Symptôme | Cause | Action |
|---|---|---|
| Toutes les requêtes bloquées « a policy on this service could not be evaluated » | Catégorie invalide dans `detect_sensitive_data`, ou erreur transitoire | Corriger la catégorie ; l'app réessaie deux fois les erreurs transitoires ; la propagation d'un changement de politique prend 3 à 5 minutes |
| 403 « Invalid scope, required scopes: ai-gateway » | Jeton de session antérieur à l'ajout du scope | Rouvrir l'app en fenêtre privée ; en attendant l'app passe sur son principal de service |
| Caractères accentués corrompus | Flux SSE décodé en Latin-1 | Le décodage UTF-8 est forcé dans l'app ; vérifié par le contrôle préalable |
| « accès refusé » à l'ouverture d'un livrable | Nom de fichier non ASCII mal encodé dans l'URL | Les noms sont ramenés en ASCII |
| Page HTML longue à produire | Livrable volumineux | Normal : 3 à 6 minutes ; la trace montre le détail |

## Coût des pièces jointes dans une conversation

Le texte des documents joints reste dans l'historique de la conversation et repart avec chaque demande suivante. C'est voulu : les questions de suivi (« et la section 3 ? ») en ont besoin, et réécrire l'historique après coup invaliderait les blocs de raisonnement du modèle. Le coût est borné : 200 000 caractères de documents par demande, et l'app arrête proprement une conversation devenue trop longue (environ 700 000 caractères) en invitant à en ouvrir une nouvelle. Pour une question ponctuelle sur un gros document, ouvrez une nouvelle conversation.
