# ⚽ FOOTBALL.AI — Prédictions de résultats

Site web de prédiction de matchs de football basé sur l'IA.

## 📁 Structure des fichiers

```
football-predictor/
├── 1_collect_data.py   → Récupère les matchs (API ou démo)
├── 2_predict.py        → Moteur de prédiction IA
├── 3_export_json.py    → Exporte les données pour le site
├── 4_auto_runner.py    → Lance le pipeline automatiquement
├── index.html          → Le site web (ouvrir dans navigateur)
├── data.json           → Données générées (ne pas modifier)
└── football.db         → Base de données SQLite
```

## 🚀 Installation

```bash
pip install numpy scipy scikit-learn requests
```

## ▶️ Utilisation rapide (données de démo)

```bash
python 1_collect_data.py    # Génère 380 matchs de démo
python 2_predict.py         # Analyse + prédictions
python 3_export_json.py     # Export JSON
```

Puis ouvrir `index.html` dans ton navigateur.

## 🔑 Utilisation avec vraies données

1. Crée un compte sur https://www.football-data.org/ (gratuit)
2. Copie ta clé API dans `1_collect_data.py` :
   ```python
   API_KEY = "ta_cle_ici"
   ```
3. Lance le pipeline :
   ```bash
   python 1_collect_data.py
   python 2_predict.py
   python 3_export_json.py
   ```

## 🤖 Mise à jour automatique

```bash
python 4_auto_runner.py
```

Lance le pipeline toutes les 6 heures. Laisse tourner en arrière-plan.

## 🧠 Algorithmes utilisés

| Algorithme | Usage |
|---|---|
| **ELO Rating** | Force relative des équipes, mis à jour match après match |
| **Régression de Poisson** | Modélise le nombre de buts attendus (xG) |
| **Analyse de forme** | 5 derniers matchs, pondération récente |
| **Head-to-Head** | Historique des confrontations directes |
| **Score de confiance** | Combinaison de tous les modèles (0-100%) |

## 📊 Compétitions disponibles

- Premier League (PL)
- Ligue 1 (FL1)
- La Liga (PD)
- Bundesliga (BL1)
- Serie A (SA)

Modifie `COMPETITIONS` dans `1_collect_data.py` pour choisir.
