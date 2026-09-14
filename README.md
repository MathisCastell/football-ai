# ⚽ FOOTBALL.AI — Prédictions de résultats

Service web de prédiction de matchs de football (Premier League, Ligue 1, La Liga, Bundesliga, Serie A) basé sur un moteur ELO + régression de Poisson enrichi de métriques avancées (xG estimé, pressing, forme, repos, blessures/suspensions). Le moteur **note automatiquement ses propres erreurs** une fois les matchs joués et **recalibre ses paramètres** en conséquence — voir [Comment le modèle apprend](#comment-le-modèle-apprend).

## 🚀 Démarrage rapide (local)

Aucune base de données à installer : par défaut le service utilise un fichier SQLite créé automatiquement.

```bash
pip install -r requirements.txt
python main.py
```

Puis ouvre **http://localhost:5000**. Le premier lancement collecte les données réelles (TheSportsDB, gratuit, sans inscription) et calcule les premières prédictions en arrière-plan — ça prend une à deux minutes.

Sur Windows, tu peux aussi double-cliquer `launch_windows.bat`.

Le site s'ouvre sur une **page de garde publique** (`/`). Il faut créer un compte (gratuit, aucune carte bancaire) pour accéder au tableau de bord (`/app`). Pour avoir un compte admin dès le premier lancement, ajoute dans `.env` :

```
ADMIN_EMAIL=admin@exemple.com
ADMIN_PASSWORD=un-mot-de-passe-solide
```

Le compte est créé (ou promu admin) automatiquement au démarrage. L'espace admin est accessible sur `/admin` une fois connecté avec ce compte.

## 🌐 Déployer en ligne (Render.com)

1. Pousse ce dépôt sur GitHub.
2. Sur [render.com](https://render.com), clique **New > Blueprint**, connecte le dépôt : Render détecte automatiquement `render.yaml` et crée le service web **et** la base PostgreSQL gratuite associée.
3. Clique **Apply** — c'est tout. `DATABASE_URL` est injectée automatiquement.
4. (Optionnel) Ajoute une variable `FOOTBALL_API_KEY` dans les paramètres du service si tu as une clé [football-data.org](https://www.football-data.org/) — sinon le service utilise TheSportsDB gratuitement.
5. Dans les paramètres du service Render, renseigne `ADMIN_EMAIL` et `ADMIN_PASSWORD` (variables marquées `sync: false` dans `render.yaml`, à saisir toi-même — jamais commitées) pour disposer d'un compte admin dès le premier déploiement.

Le service tourne en continu et relance automatiquement son pipeline (collecte → prédiction → apprentissage) toutes les `PIPELINE_INTERVAL_HOURS` heures (6 par défaut, configurable dans `render.yaml`). `SECRET_KEY` (signature des sessions) est généré et conservé automatiquement par Render.

Un `Dockerfile` est aussi fourni pour déployer ailleurs (Fly.io, VPS...) sans changement de code.

## 🧠 Comment le modèle apprend

Contrairement à un modèle statique, le moteur mesure sa propre erreur et s'auto-corrige :

1. **Traçabilité** — chaque prédiction générée pour un match à venir est gelée dès que le match est joué (elle n'est plus régénérée).
2. **Notation automatique** — dès qu'un match se termine, le pipeline compare la prédiction gelée au résultat réel et calcule un score de Brier, une log loss, et si le favori annoncé était le bon.
3. **Recalibrage automatique**, à chaque cycle :
   - **Walk-forward tuning** : rejoue l'historique des matchs avec différentes valeurs du facteur K (ELO) et de l'avantage domicile, et garde la combinaison qui minimise l'erreur réelle mesurée.
   - **Calibration Platt** : une fois assez de prédictions notées (40 par défaut), une régression logistique corrige un éventuel biais de sur/sous-confiance dans les probabilités affichées.
4. **Transparence** — l'onglet **Fiabilité** du site (et la route `/api/model-performance`) affiche le taux de réussite du favori, l'évolution du score de Brier dans le temps, et les hyperparamètres actuels par compétition.

Ce mécanisme a besoin de matchs joués pour produire ses premières statistiques : au tout début (base vide), l'onglet Fiabilité reste vide, le temps que des prédictions faites se réalisent.

## 👤 Comptes, freemium et espace admin

- **Gratuit** (inscription libre, `/login?mode=register`) : résultats, classement, prédictions 1X2 de base, limité aux championnats listés dans `FREE_COMPETITIONS` (Premier League + Ligue 1 par défaut).
- **Premium** : tous les championnats, xG/pressing, conseils de paris, page Fiabilité du modèle. **Attribué manuellement** par un admin depuis `/admin` (pas de paiement en ligne pour l'instant — le code est structuré pour brancher un système comme Stripe plus tard sans tout refaire).
- **Espace admin** (`/admin`, réservé aux comptes admin) : liste des comptes avec bascule premium/admin, messages du formulaire de contact, statut du pipeline et déclenchement manuel d'une collecte.
- Le premier compte admin est créé automatiquement au démarrage depuis `ADMIN_EMAIL`/`ADMIN_PASSWORD` (voir Démarrage rapide). Les admins suivants sont promus depuis l'espace admin.
- Le formulaire de contact (page d'accueil) écrit dans une table `support_messages`, consultable uniquement dans l'espace admin — aucun email n'est envoyé.

## 📁 Structure du projet

```
football-ai/
├── main.py                # Point d'entrée (serveur web + planificateur)
├── app/
│   ├── config.py            # Variables d'environnement
│   ├── db.py                 # Connexion SQLAlchemy (Postgres prod / SQLite dev)
│   ├── models.py              # Schéma de données (matchs, prédictions, comptes...)
│   ├── auth.py                 # Sessions, décorateurs d'accès, bootstrap admin
│   ├── server.py                 # Fabrique l'app Flask (routes + auth + DB)
│   ├── collectors/                # Collecte incrémentale des matchs
│   ├── prediction/                 # ELO, Poisson, facteurs de forme/H2H/repos/blessures
│   ├── learning/                    # Notation des erreurs + recalibrage automatique
│   ├── pipeline.py                   # Orchestration collecte → prédiction → apprentissage
│   └── api.py                         # Routes HTTP (données, admin, support, pages)
├── landing.html            # Page de garde publique
├── login.html               # Connexion / inscription
├── index.html                 # Tableau de bord (/app, connexion requise)
├── admin.html                   # Espace admin (/admin)
├── tests/                    # pytest — ELO, Poisson, calibration, auth, gating, pipeline
├── render.yaml                # Déploiement Render (service web + Postgres)
└── Dockerfile                  # Déploiement alternatif (Fly.io, VPS...)
```

## 🔑 Sources de données

Par défaut, aucune clé n'est requise : les matchs des 5 grands championnats sont récupérés gratuitement via [TheSportsDB](https://www.thesportsdb.com/). Si une variable `FOOTBALL_API_KEY` ([football-data.org](https://www.football-data.org/), inscription gratuite) est définie, elle prend le dessus (le plan gratuit de football-data.org ne couvre en général que la Premier League).

## ⚙️ Variables d'environnement

Voir `.env.example`. Toutes sont optionnelles en local.

| Variable | Rôle | Défaut |
|---|---|---|
| `DATABASE_URL` | Postgres en prod, sinon SQLite local automatique | (SQLite `football.db`) |
| `FOOTBALL_API_KEY` | Clé football-data.org (optionnelle) | — |
| `PIPELINE_INTERVAL_HOURS` | Fréquence du cycle collecte/prédiction/apprentissage | `6` |
| `MIN_SAMPLES_FOR_CALIBRATION` | Échantillon minimum avant calibration Platt | `40` |
| `SECRET_KEY` | Signature des sessions (générée automatiquement en prod sur Render) | (aléatoire en dev) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Compte admin créé au démarrage s'il n'existe pas | — |
| `FREE_COMPETITIONS` | Championnats accessibles aux comptes gratuits | `Premier League,Ligue 1` |

## 🧪 Tests

```bash
pip install pytest
pytest tests/
```

Couvre le système ELO, le modèle de Poisson, la calibration, et un test bout-en-bout du pipeline (sans appel réseau, sur base SQLite temporaire).

## 🤖 Bot Discord (mis de côté pour l'instant)

`bot.py` reste fonctionnel **en local uniquement** (il lit directement le fichier `football.db`). Il n'est pas encore adapté à la base PostgreSQL de production — à prévoir si le Discord redevient prioritaire.

## 🧮 Algorithmes utilisés

| Algorithme | Usage |
|---|---|
| **ELO Rating** | Force relative des équipes, paramètres auto-calibrés |
| **Régression de Poisson** | Buts attendus, ajustée par xG estimé, pressing, solidité défensive, fatigue |
| **Forme pondérée** | 5 derniers matchs, globale et scindée domicile/extérieur |
| **Head-to-Head** | Historique des confrontations directes |
| **Repos / fatigue** | Jours depuis le dernier match de chaque équipe |
| **Compositions & suspensions** | Dernière composition connue, cartons rouges récents |
| **Calibration Platt + walk-forward tuning** | Auto-correction du modèle à partir de ses erreurs passées |
