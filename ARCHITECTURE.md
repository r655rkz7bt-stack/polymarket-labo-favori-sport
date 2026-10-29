# Architecture du laboratoire « favori de sport »

Copie élaguée (28/10/2026) du système de trading de « Polymarket Autonomous Lab ». Retiré : toute la partie recherche
(expériences, tests sur le passé, enquêtes, feuille de mission, routine du chercheur), les autres stratégies et les
sources externes (actualités, météo, crypto, bookmakers…) ; puis, le 29/10/2026 (décision d'Etienne), l'arène, les
portefeuilles fantômes, la santé des stratégies et les récompenses estimées. Gardé à l'identique : le noyau protégé
(journal signé, contrôleur, simulateur d'exécution, frais, audit, réinitialisation) et le gardien des pull requests.

## Le cycle (`scripts/run_simulation.py` → `main.run_cycle`)
1. Journal signé du capital principal relu et vérifié (`ledger.py`) ; faillite = arrêt. Ordres posés laissés par les
   anciens fantômes : annulés (`Database.cancel_orders_except`).
2. Règlements des marchés terminés (`settlement.py`, marchés lus par lots).
3. Scanner (`market_scanner.py`, Gamma) : 1 500 marchés, les 150 plus échangés surveillés ; carnets (CLOB).
4. Ordres posés des cycles précédents : servis si un vendeur traverse leur prix, annulés à l'expiration, à la fermeture du
   marché ou, si la stratégie le demande, au coup d'envoi (`execution_simulator.simulate_resting_buy`).
5. Santé (`sante.py`) : facteur de mise aux règles de l'ancien fantôme (prudence × renfort, ou 0 si suspendue), calculé
   sur ses antécédents (`antecedents.py`, journal du labo d'origine relu et sa chaîne de hash vérifiée) puis sur le
   journal d'ici. Stratégie (`strategy_engine.py` : `NaiveFavorite`, enveloppée par `Filtered` puis `PatientOrders`) :
   achats immédiats et ordres à poser, mises × ce facteur ; deux camps d'un même match refusés, mise plafonnée
   (`principal.max_mise`).
6. Contrôleur de sécurité (`safety_controller.py`), relecture du carnet (`relecture.chute_max`), réserve de liquidités
   si `principal.reserve_liquidites` est réglée (pas pour l'instant : le fantôme n'en avait pas), simulation sur les vrais carnets, écriture au journal, battement de cœur.
7. Tableau de bord (`dashboard.py`, `dashboard_template.html`, `today.py`) toutes les 10 minutes.

## Données
Journal signé : branche `data` (`ledger/ledger.jsonl`) ; base d'analyse : cache GitHub Actions + sauvegarde quotidienne dans
`data` ; tableau de bord : branche `gh-pages`. Détails d'exploitation : `docs/EXPLOITATION.md`.

## Confiance (`confiance.py`)
Sur les opérations clôturées des antécédents puis du capital principal : score t des rendements par pari (résultat ÷ mise), confiance en %
(`confidence_percent`), confiance par jour (gagnés contre attendus, journée par journée), verdict (« prometteuse »,
« indéterminée », « ne fonctionne pas ») d'après l'intervalle à 90 % du rendement moyen, par bootstrap sur les matchs ;
« données insuffisantes » sous 20 opérations. Les courbes du tableau de bord (bouton « Voir ses courbes ») refont ces calculs
dans le navigateur, à partir du journal public ; `tests/test_dashboard.py` vérifie que les deux donnent les mêmes chiffres.
