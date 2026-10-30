# Personnaliser l'assistant pour un client

## 1. Créer la marque

```bash
cp -R brands/acme brands/<client>
```

Éditez `brands/<client>/brand.yaml` :

- **Libellés** : `name` (nom de l'assistant), `company`, `site`, `site_short`, `org_label`, `description` (une phrase utilisée dans le prompt système).
- **Couleurs** : `primary` (en-têtes, boutons), `primary_2`, `accent` (onglet actif, filets), `secondary` (liens). Gardez un contraste suffisant entre `primary` et le blanc.
- **Polices** : `heading` et `body` pour le web ; `office` pour PowerPoint et Word. Pour Office, choisissez une police présente sur les postes du client (Segoe UI, Calibri) : une police web absente du poste serait remplacée à l'ouverture.
- **Services** (`departments`) : apparaissent dans l'interface et servent d'étiquettes de requête dans la gateway et le tableau de bord.
- **Modèles de démarrage** (`templates`) : `kind` vaut `pptx`, `docx` ou `html` ; `prompt` est envoyé tel quel.
- **Suggestions** par service et **consignes métier** (`instructions`) : règles ajoutées au prompt système, rédigées à l'impératif.

## 2. Logos

Trois fichiers PNG à fond transparent dans le dossier de marque :

| Fichier | Usage | Conseil |
|---|---|---|
| `wordmark.png` | Barre latérale, diapositives de contenu, en-tête Word (fond clair) | environ 840 x 96 px |
| `wordmark_on_dark.png` | En-tête des pages HTML, couverture PowerPoint (fond `primary`) | même format, version blanche |
| `mark.png` | Avatar de l'assistant, favicon | carré, 256 x 256 px |

## 3. Modèles Office

`install/office_templates.py` génère depuis la marque :

- `templates/deck.pptx` : une couverture (panneau `primary`, filet `accent`, logo sur fond foncé) et quatre diapositives de contenu (bandeau, filet, logo, zone `[contenu]`, pied « <company> · <site> · Diffusion interne ») ;
- `templates/8d.docx` : en-tête avec logo, tableau d'en-tête (N° 8D, Date d'ouverture, Pilote, Ligne / Poste), sections D1 à D8.

Test hors installation :

```bash
python3 install/office_templates.py brands/<client> /tmp/out
```

Pour utiliser un vrai modèle du client, gardez la même structure : le moteur de rendu repère la zone dont le texte est exactement `[contenu]` et le pied qui commence par le nom de l'entreprise suivi de ` ·`.

## 4. Données et gouvernance

Dans `config/assistant.yaml` :

- `data.scope` : décrivez les tables que Genie One peut interroger, en termes métier ;
- `governance.ip_leak_policy.keywords` : mots-clés qui doivent bloquer une demande (références de plans, mentions de confidentialité) ;
- `governance.builtin_policies.sensitive_data.categories` : uniquement des catégories valides (`class.email_address`, `class.phone_number`, `class.iban_code`, `class.credit_card`, …). Une catégorie inconnue fait refuser toutes les requêtes.

## 5. Vérifier

Relancez l'installateur, puis :

- ouvrez l'app en fenêtre privée, posez une question de données et une demande de document ;
- envoyez une demande contenant un e-mail et un IBAN fictifs : un bandeau vert « Données sensibles protégées » apparaît et la trace montre `ANONYMISATION` ;
- envoyez une demande contenant un mot-clé de la politique anti-fuite : la demande est bloquée avec la raison configurée.
