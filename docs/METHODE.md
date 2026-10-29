# Méthode de travail

Fichier libre : Claude peut l'améliorer. Les règles permanentes sont dans `CLAUDE.md`.

## Méthode de travail

- Tests avec `pytest`. Tout défaut découvert donne lieu à un test de non-régression.
- Petits commits clairs ; branches `claude/` et pull requests vers `main`, fusionnées automatiquement si `guard` et `tests` passent.
- Dépendances peu nombreuses, versions épinglées dans `requirements.txt`.
- Avant d'utiliser un endpoint Polymarket, vérifie sa documentation actuelle sur docs.polymarket.com : frais et limites changent.
- Si un domaine réseau est bloqué (erreur 403 `host_not_allowed`), dis à Etienne exactement quel domaine ajouter.

## Tester une idée sur la stratégie

- Une idée = une **variante** dans `config/strategies.yaml` : même `class: naive_favorite`, un autre nom qui commence
  par `nf_sport_patiente_prix_850_875` (il est alors protégé du retrait pour inactivité), d'autres réglages (`params`),
  d'autres `filtres` (familles, plage de prix, heures avant le match, issue…), ou `annuler_au_coup_d_envoi: true`.
  Écrire l'hypothèse (`hypothesis`) et ce qu'une perte apprendrait.
- Chaque variante a son propre fantôme (1 000 € fictifs) : on la compare à l'originale **sur la même période**, avec la
  confiance de l'arène (score t, pourcentage, verdict) et les gagnés contre attendus, jamais sur quelques paris.
- Le capital principal suit automatiquement la meilleure (règles : `config/promotion.yaml`).

## Commandes

- Installer : `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests : `.venv/bin/python -m pytest -q`
- Initialiser le journal (une seule fois) : `.venv/bin/python scripts/initialize.py --dev-key`
- Un cycle unique : `.venv/bin/python scripts/run_simulation.py --once --dev-key`
- Rapport : `.venv/bin/python scripts/generate_report.py --dev-key`
- Tableau de bord : `.venv/bin/python scripts/build_dashboard.py --dev-key` (sortie `site/`, régénéré par le cycle toutes les 10 min)
- Aperçu avant publication (vraies données publiées + gabarit du dépôt, photos téléphone et ordinateur) :
  `bash scripts/fetch_published.sh` puis `.venv/bin/python scripts/apercu_dashboard.py` (sortie `apercu/`)
- Rapports publiés par le cycle de production : `bash scripts/fetch_published.sh` (dossier `published/`)
- Production : workflows `cycle` (toutes les 2 min, = `cycle.bucket_minutes`), `initialize` (une fois), exploitation dans `docs/EXPLOITATION.md`
- Réinitialisation (Etienne seul, via le workflow `reset-capital`) : `scripts/reset_capital.py`
- Contrôle des fichiers protégés : `scripts/guard_check.py PROTECTED_PATHS.txt < liste_des_fichiers`
- `--dev-key` : clé locale de test (`state/dev_ledger_key`, ignorée par Git). En production, `LEDGER_KEY` vient de l'environnement GitHub `production`.

