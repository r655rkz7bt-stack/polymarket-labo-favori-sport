# Reprendre le capital à la valeur de l'ancien fantôme (Etienne seul)

Demande d'Etienne (29/10/2026) : « mettre les mêmes conditions que sur l'ancien fantôme, comme si c'était un fantôme et
que tu reprenais la suite juste après son passé dans l'autre labo ».

## Ce que Claude a déjà fait (libre)
- Mise aux règles du fantôme (`sante.py`, `config/settings.yaml` → `sante_strategies`, `principal`) : Kelly entier ×
  prudence (½ tant que rien n'est prouvé, 1 prouvée, ¼ démentie) × renfort (jusqu'à 2), mise ≤ 25 % du capital, pas de
  réserve, ordres posés gardés pendant le match.
- Son passé (`archives/labo_origine/`, 20/10 → 28/10/2026 23 h 42) est relu à chaque cycle (`antecedents.py`) : ses
  85 paris comptent dans la confiance et dans sa mise. Calculé sur ce passé, il misait Kelly × 1 à la fin (prévisions
  prouvées, pas de renfort : confiance par jour 78 % < 80 %).

## Ce que seul Etienne peut faire : le capital
Le journal signé (noyau protégé) a commencé à 1 000 € le 29/10. Pour qu'il reprenne la valeur du fantôme, il faut une
remise à zéro (RESET, inscrite et signée dans le journal, avec sa raison) au montant de la **dernière valeur signée du
fantôme : 1 472,59 €** (28/10/2026 23 h 42 : 1 248,23 € disponibles + sa dernière position, « FUT gagne le PGL Masters
Bucarest : NON », 275,62 parts comptées à leur prix de revente du moment). Cette position ne se règle pas ici : elle
est comptée comme revendue à ce prix.

Le montant d'une remise à zéro vient de `config/risk_limits.yaml` (protégé), et trois tests protégés vérifient qu'il
vaut 1 000 : on le change donc **le temps de la remise à zéro seulement**.

À faire **avant** de fusionner la pull request (tant que le capital n'a aucune position : une remise à zéro efface
les positions ouvertes) :
1. `config/risk_limits.yaml` → `initial_capital: 1000` devient `initial_capital: 1472.59` (commit sur `main`).
2. Actions → **reset-capital** → code `RESET_CODE`, raison : « Reprise de l'ancien fantôme
   nf_sport_patiente_prix_850_875 : dernière valeur signée 1 472,59 au 28/10/2026 23 h 42 ».
3. `config/risk_limits.yaml` → remettre `initial_capital: 1000` (commit sur `main`).
4. Fusionner la pull request.
Le contrôle `tests` est rouge entre les étapes 1 et 3 (normal) ; il redevient vert à l'étape 3.

Après coup : le tableau de bord montre la courbe du fantôme (départ 1 000 € le 20/10) suivie de celle d'ici, et
« depuis le départ » se compte depuis ces 1 000 €. Le palier suivant du journal devient 1,5 × 1 472,59 = 2 208,89
(le fantôme visait 2 250 après avoir touché 1 500 le 26/10) : le cliquet est dans le code protégé.
