# Méthode de travail

Fichier libre : Claude peut l'améliorer. Les règles permanentes sont dans `CLAUDE.md`.

## Méthode de travail

- Tests avec `pytest`. Tout défaut découvert donne lieu à un test de non-régression.
- Petits commits clairs ; branches `claude/` et pull requests vers `main` (fusion automatique si `guard` et `tests` passent et si « Allow auto-merge » est activé, sinon Etienne clique « Merge »).
- Dépendances peu nombreuses, versions épinglées dans `requirements.txt`.
- Avant d'utiliser un endpoint Polymarket, vérifie sa documentation actuelle sur docs.polymarket.com : frais et limites changent.
- Si un domaine réseau est bloqué (erreur 403 `host_not_allowed`), dis à Etienne exactement quel domaine ajouter.

## Changer un réglage de la stratégie

- Depuis le 29/10/2026 il n'y a plus d'arène : la stratégie trade directement le capital principal, et un changement de
  réglage s'applique à ce capital. Il n'y a donc plus de comparaison « sur la même période » avec une variante.
- Réglages : `config/strategies.yaml` (`params` bornés : biais, incertitude, avantage minimal, `kelly_fraction` ; `filtres` :
  familles, plage de prix, heures avant le match, heure UTC, écart, un seul match, mots exclus ; `annuler_au_coup_d_envoi`)
  et `config/settings.yaml` → `principal` (mise maximale, réserve de liquidités).
- Avant de changer : écrire pourquoi, et ce qu'on attend. Après : noter la date dans `docs/CONTEXTE.md` (journal des
  réglages), pour lire les résultats (confiance, gagnés contre attendus) avant et après, jamais sur quelques paris.
- Vérifier : `pytest`, puis un cycle local (`run_simulation.py --once --dev-key`) pour voir les abstentions de la stratégie.

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

