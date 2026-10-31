# Laboratoire « favori de sport » : nf_sport_patiente_prix_850_875

**📊 Tableau de bord : [r655rkz7bt-stack.github.io/polymarket-labo-favori-sport](https://r655rkz7bt-stack.github.io/polymarket-labo-favori-sport/)**

Trading **simulé** sur Polymarket (1 000 € virtuels, jamais d'argent réel), centré sur une seule stratégie, exportée le
28/10/2026 du laboratoire [Polymarket Autonomous Lab](https://github.com/r655rkz7bt-stack/Polymarket-autonomus-lab), où elle était
n° 1 de l'arène (1 000 → 1 484 € fictifs en 8,3 jours, 85 opérations, confiance 90 %, au 28/10 23 h 48 UTC).
**Tout le contexte (origine, chiffres, mises en garde, mise en service, prochaines étapes) : `docs/CONTEXTE.md`.**

**La stratégie** : sur un match de sport ou d'e-sport, acheter le favori en posant un ordre 1 cran au-dessus du meilleur
acheteur (on attend un vendeur, on paie moins cher, souvent sans frais), seulement quand le prix du vendeur est entre 0,85 et
0,875. Hypothèse : à ce prix, le favori gagne un peu plus souvent que ce que dit le marché. Attention : elle était la meilleure
de plusieurs dizaines de stratégies de favoris, une part de son avance peut venir de la chance. Ce laboratoire sert à le vérifier.

## Ce qui tourne
- **Cycle** toutes les 5 minutes, comme dans l'ancien labo (GitHub Actions, lancé par cron-job.org) : lecture des marchés et des carnets publics de
  Polymarket, propositions de la stratégie, contrôleur de sécurité, simulation d'exécution sur les vrais carnets (frais
  compris), journal signé.
- **Un seul portefeuille** (depuis le 29/10/2026, plus d'arène) : la stratégie trade directement le capital principal,
  **aux conditions de son ancien fantôme** (Kelly × prudence × renfort, `sante.py` ; une mise au plus 25 % du capital) et
  en **reprenant la suite de son passé** : son journal du labo d'origine (`archives/labo_origine/`) est relu à chaque
  cycle (`antecedents.py`) ; ses paris comptent dans la confiance et la mise, le capital part de 1 000 €.
- **Les mêmes marchés que le fantôme** : le scanner de l'ancien labo (réglages identiques, pages par étiquette comprises)
  et les demandes de marchés de ses voisines de l'ancien labo (`voisines.py`, `config/voisines.yaml`), sans leurs paris.
- **Indices de confiance** de la stratégie, calculés sur ses paris terminés, passé compris (`confiance.py`) : score t,
  pourcentage, confiance par jour, gagnés contre attendus, verdict.
- **Règle n°1** : à 0 € disponible, le trading s'arrête définitivement (code protégé `ledger.py`, `safety_controller.py`).
- **Tableau de bord** sur GitHub Pages : capital, la stratégie et sa confiance (avec ses courbes), positions, ordres posés, journal.

## Mise en service
Une seule fois, pas à pas : `docs/EXPLOITATION.md` (secrets, initialisation, Pages, cron-job.org).

## Changer un réglage
`config/strategies.yaml` (biais, plage de prix, mise, annulation au coup d'envoi…) et `config/settings.yaml` (plafonds) ;
voir `docs/METHODE.md`.

## Fichiers
- `src/polymarket_agent/` : moteur (scanner, stratégie, contrôleur, simulateur, journal, confiance, tableau de bord) ;
- `config/` : stratégie, réglages, garde-fous, mise en page ; `scripts/` : initialisation, cycle, réinitialisation ;
- `CLAUDE.md` : règles permanentes ; `PROTECTED_PATHS.txt` : noyau que seul Etienne modifie ; `ARCHITECTURE.md` : technique.
