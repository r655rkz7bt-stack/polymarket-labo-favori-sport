# Contexte du laboratoire « favori de sport »

À lire en premier par toute nouvelle session (Claude ou humain). Écrit le 29/10/2026 lors de l'export.

## 1. D'où vient ce dépôt
- Origine : [r655rkz7bt-stack/Polymarket-autonomus-lab](https://github.com/r655rkz7bt-stack/Polymarket-autonomus-lab), le
  « Polymarket Autonomous Lab » d'Etienne : trading **simulé** sur Polymarket, 1 000 € virtuels, une arène de ~180 stratégies
  en portefeuilles fantômes, un capital principal qui suit les meilleures, et une routine « chercheur » qui teste des idées.
- Demande d'Etienne (28/10/2026) : « exporter nf_sport_patiente_prix_850_875 dans un nouveau dépôt, avec tout le système
  complet de trading ; mettre de côté tous les éléments de recherche et d'amélioration ; un laboratoire centré sur cette
  stratégie, qui tourne à part pour tester des choses ; garder les indices de confiance ; retirer tout ce qui ne concerne
  pas cette stratégie ».
- Nom prévu du dépôt : `r655rkz7bt-stack/polymarket-labo-favori-sport` (déjà écrit dans `config/dashboard.yaml` →
  `journaux_url` et dans `docs/EXPLOITATION.md`). Si le nom change, mettre à jour ces deux endroits.

## 2. La stratégie
`nf_sport_patiente_prix_850_875` (classe `naive_favorite`, `config/strategies.yaml`) :
- marchés de **sport et d'e-sport** (famille `sports`, `strategy_engine.market_family`) ;
- côté acheté : le **favori** ; estimation = prix + biais 0,05 (`bias`), bornes ± 0,015 (`uncertainty`) ;
- **mode patient** (`limit_ticks: 1`) : ordre posé 1 cran au-dessus du meilleur acheteur, servi seulement si un vendeur
  traverse ce prix (`execution_simulator.simulate_resting_buy`) ; souvent sans frais (maker) ;
- **filtre de prix** : il porte sur le prix du vendeur (ask), entre 0,85 et 0,875 ; l'ordre posé peut donc être un peu plus
  bas (premier achat : 0,84) ; mise de Kelly sur l'avantage supposé (ici comme dans son ancien fantôme : Kelly × prudence × renfort, `sante.py`).
Historique : favori naïf → « sport » → « patient » (17/10) → morceau de prix 0,850-0,875 trouvé par l'entonnoir 80/20 (20/10).

### Chiffres dans le labo d'origine (fantôme, vrais carnets, frais compris)
| date (UTC) | opérations | capital fictif | confiance (t) | verdict |
|---|---|---|---|---|
| 27/10 23 h 17 | 74 | 1 565,72 € | 2,16 (98 %) | prometteuse |
| 28/10 23 h 48 | 85 | 1 484,44 € | 1,26 (90 %) | indéterminée (le résultat peut être dû au hasard) |

Relu dans son journal signé (`archives/labo_origine/`, 20/10 15 h 51 → 28/10 23 h 42) : **85 positions réglées, 76,0
gagnées pour 72,5 attendues par leurs prix (z +1,08)**, +484 € sur 11 132 € misés (+4,3 % par euro), prix moyen payé 0,852,
8 perdues. Selon l'instant où l'ordre a été servi : 66 servis après le coup d'envoi +194 € (+2,3 %), 19 avant +291 € (+10,2 %).

### Mises en garde (honnêteté)
- Elle était la **meilleure de plusieurs dizaines** de stratégies de favoris presque identiques : une « championne » de 35
  à 60 stratégies a un z ≈ 2 **sans aucun avantage** (biais de sélection). Le recul du 28/10 (98 % → 90 %) va dans ce sens.
- Sur le passé (labo d'origine, 50 232 paris de favoris avant le match sur 30 000 marchés tirés au hasard), **aucune tranche
  de prix de favori n'était régulièrement gagnante** ; 0,85-0,875 à 3 h : −1,2 % (435 paris).
- **Ordres posés servis pendant le match** : dans tout le labo d'origine ils perdaient −9,8 % par pari (320 jetons, z −2,8),
  contre −0,3 % pour un achat immédiat : le marché vient « traverser » notre prix surtout quand le match tourne mal pour le
  favori (sélection adverse). Le réglage `annuler_au_coup_d_envoi: true` (config/strategies.yaml) fait jouer la stratégie
  avant le match seulement (ordres annulés au coup d'envoi, aucun nouveau pari pendant le jeu). Il est à `false` : comme
  dans l'ancien labo, où elle gagnait aussi pendant le match (+2,3 %), moins qu'avant (+10,2 %).
- Mises en Kelly sur un biais supposé de 0,05 : grosses mises à prix élevé. Comme pour son fantôme : Kelly × santé
  (`sante.py`) et une mise au plus 25 % du capital (`settings.yaml` → `principal.max_mise`).

## 3. Ce qui a été gardé, retiré, changé (export du 28/10 ; voir §6 pour la suite)
- **Gardé tel quel** : noyau protégé (`PROTECTED_PATHS.txt` : journal signé, contrôleur, règle n°1, simulateur, frais,
  audit indépendant, réinitialisation, gardien des pull requests), cycle, scanner, arène (classement, confiance,
  verdicts, champion(s), parts du capital principal, renfort), santé des stratégies, récompenses de liquidité estimées,
  sélection adverse (markout), tableau de bord, alerte « le cœur ne bat plus », tests correspondants.
- **Retiré** : toute la recherche (dossiers `experiments/`, `proposals/`, tests sur le passé, enquêtes, entonnoir 80/20,
  feuille de mission, routine du chercheur, `requirements-recherche.txt`), les ~180 autres stratégies (`strategies_v2/v3`,
  calibration, crypto…), toutes les sources externes (actualités, météo, crypto, bookmakers, Kalshi…), les références
  rattachées aux marchés et les pages supplémentaires du scanner. Aucune clé d'API n'est nécessaire.
- **Changé** : `config/strategies.yaml` (2 stratégies), `settings.yaml` (sources Polymarket seules, pas de pages en plus),
  `promotion.yaml` (`jamais_retirer: [nf_sport_patiente_prix_850_875]`, préfixe : couvre toutes les variantes),
  `dashboard.yaml` (« À la une », adresse des journaux), `cycle.yml` (seule `LEDGER_KEY`, plus de feuille de mission),
  docs. 331 tests verts ; un cycle local complet a tourné (1 500 marchés lus, tableau de bord sans erreur).
- **Non repris** : l'historique signé (il est signé avec la `LEDGER_KEY` de l'autre labo ; un journal ne s'importe pas).
  Le labo repart de 1 000 € ; le journal d'origine est joint en **archive en lecture seule** (`archives/labo_origine/`).
  Le capital principal ne suit une stratégie qu'après 12 opérations, +2 % et une confiance suffisante (`promotion.yaml`).

## 4. Mise en service (une fois, par Etienne, une étape à la fois)
Détail : `docs/EXPLOITATION.md`. Résumé :
1. Créer le dépôt **vide** `polymarket-labo-favori-sport` (public : GitHub Pages gratuit), sans README.
2. Y pousser ce code sur `main` (voir §5).
3. **Settings → Environments → New environment** `production` ; secrets `LEDGER_KEY` (longue phrase) et `RESET_CODE`.
   Ne jamais les coller dans une conversation.
4. **Actions** → workflow **initialize** → *Run workflow* → taper `INITIALISER`. Vérifier : la branche `data` apparaît.
5. **Actions** → **cycle** → *Run workflow*. Vérifier : coche verte, commit sur `data`, branche `gh-pages`.
6. **Settings → Pages** : branche `gh-pages`, dossier `/` → tableau de bord à
   `https://r655rkz7bt-stack.github.io/polymarket-labo-favori-sport/`.
7. Ruleset `protection-data` (pas de suppression ni de force push sur `data`), puis `protection-main` (`docs/VERROUILLAGE.md`).
8. cron-job.org : nouveau jeton GitHub (fine-grained, ce seul dépôt, permission Actions) et tâche toutes les 5 minutes (comme l'ancien labo, = `cycle.bucket_minutes`) sur
   `…/repos/r655rkz7bt-stack/polymarket-labo-favori-sport/actions/workflows/cycle.yml/dispatches` ; le jeton ne passe jamais
   par la conversation.
9. Facultatif : installer l'application GitHub de Claude sur ce dépôt pour qu'une session puisse y travailler.

## 5. Pour la prochaine session Claude
- Ce code est rangé dans la branche `claude/labo-favori-sport` du dépôt d'origine (branche orpheline : son contenu est
  exactement ce dépôt). Pour le publier : ajouter le dépôt d'origine et le nouveau à la session, puis
  `git fetch origin claude/labo-favori-sport` et pousser ce commit sur `main` du nouveau dépôt
  (`git push <nouveau> FETCH_HEAD:refs/heads/main`). Une session Claude ne peut pas créer de dépôt GitHub (403) : Etienne le crée.
- Commandes locales : `docs/METHODE.md` (installer, tests, `initialize.py --dev-key`, `run_simulation.py --once --dev-key`).
- Idées de réglages à essayer (un à la fois, date notée au §6, voir `docs/METHODE.md`) : `annuler_au_coup_d_envoi: true`
  (avant le match seulement) ; biais plus faible (0,03) pour des mises plus raisonnables ; mise plus petite
  (`kelly_fraction`) ; par sport (tennis, football, e-sport) ; heures avant le match (`heures_min`, `heures_max`).
- Règles permanentes : `CLAUDE.md` (règle n°1, interdits, noyau protégé, qui est Etienne : expliquer simplement en
  français, une étape à la fois, ne jamais demander un secret).

## 6. Journal des changements (après l'export)
- **29/10/2026, mise en service** : dépôt créé, secrets, `initialize` (après une `LEDGER_KEY` trop courte : 32 caractères
  au moins), ruleset `protection-data`, premier cycle, GitHub Pages, cron-job.org **toutes les 2 minutes** (choix d'Etienne ;
  `cycle.bucket_minutes: 2` en conséquence).
- **29/10/2026, plus d'arène** (demande d'Etienne : « la stratégie va juste trader toujours sur le principal, vire tout ce qui
  ne sert plus à rien ») :
  - `nf_sport_patiente_prix_850_875` trade directement le capital principal ; la sœur « avant le match » est retirée
    (son idée reste un réglage : `annuler_au_coup_d_envoi`, qui suffit désormais seul) ;
  - mise : demi-Kelly (`kelly_fraction: 0.5`, la mise de base de son ancien fantôme tant que son avantage n'était pas
    prouvé), au plus 25 % du capital par pari, 3 % toujours gardés disponibles ; plus de plafond par famille (tous ses
    paris sont du sport) ; ordres posés gardés pendant le match, comme dans l'ancien labo ;
  - retirés : arène, fantômes, `promotion.yaml`, copie du principal, santé et renfort des stratégies, récompenses estimées
    (liquidité, détention), historique du classement, entonnoir, modules et tables morts (sources, IA, références) ;
  - gardés : indices de confiance (nouveau `confiance.py`, mêmes formules que l'arène), sélection adverse (markout),
    deux camps d'un même match, relecture du carnet, réserve, audit, tableau de bord (bloc « La stratégie » et ses courbes) ;
  - les journaux des deux fantômes créés le 29/10 restent dans la branche `data` (`ledger/fantome/`) mais ne bougent
    plus ; leurs ordres posés encore ouverts sont annulés au premier cycle ;
  - vérifié : 258 tests verts, un cycle local réel (1 496 marchés lus, 150 surveillés), tableau de bord sans erreur sur
    téléphone et ordinateur, courbes chargées depuis le journal de production.
  - `CLAUDE.md` (protégé) parle encore de l'arène : proposition de texte dans `proposals/2026-10-29_claude_md_sans_arene.md`.
- **29/10/2026, mêmes conditions que l'ancien fantôme, suite de son passé** (demande d'Etienne : « comme si c'était un
  fantôme et que tu reprenais la suite juste après son passé dans l'autre labo ») :
  - mise : Kelly entier × santé de la stratégie (`sante.py`, réglages `sante_strategies` de l'ancien labo : ½ tant que
    20 prévisions ne sont pas jugées, 1 si prouvées meilleures que le prix, ¼ sinon ; renfort jusqu'à 2 ; suspension si
    les frais mangent l'avantage) ; mise ≤ 25 % du capital ; plus de réserve de 3 % (le fantôme n'en avait pas) ;
  - antécédents : son journal du labo d'origine est relu à chaque cycle (`antecedents.py`, chaîne de hash vérifiée) ;
    ses 85 paris comptent dans la confiance et dans la mise. Sur ce passé : prudence 1 (85 prévisions jugées, avance
    +0,0019), renfort 1 (confiance par jour 78 % < 80 %), donc Kelly × 1, comme à la fin de son journal ;
  - capital : il reste à 1 000 € (décision d'Etienne) ; seuls les paris du fantôme comptent (confiance et mise) ;
  - plus aucune règle propre au capital principal de l'ancien labo (mise ≤ 10 %, plafond par famille, réserve de 3 %,
    copie, annulation au coup d'envoi) ;
  - vérifié : 262 tests verts, cycle local réel (facteur de mise 1,0).
- **29/10/2026, « comme le fantôme exactement »** (Etienne) :
  - cycle toutes les 5 minutes, comme l'ancien labo (`cycle.bucket_minutes: 5`, cron-job.org réglé par Etienne) ;
  - mêmes marchés vus : le scanner de l'ancien labo et ses réglages exacts (pages par étiquette, marchés longs, échus,
    séries d'e-sport : ~4 900 marchés lus) ; puis les marchés de sport demandés par ses voisines de l'ancien labo
    (`voisines.py`, code copié mot pour mot, `config/voisines.yaml` : 17 stratégies actives, sans leurs paris). Sur ses
    86 marchés achetés, beaucoup d'e-sport et de tennis ITF peu échangés venaient de ces demandes. Vérifié : sur les
    mêmes réponses de Polymarket, l'ancien code et celui-ci montrent au fantôme les mêmes 57 marchés de sport ;
  - limites : les demandes qui dépendent de sources externes (calendrier NHL, Kalshi, PredictIt, Manifold, tweets) ne
    sont pas reprises ; l'ancien labo continue d'évoluer : `config/voisines.yaml` est sa photo du 29/10/2026 ;
  - vérifié : cycle local réel en 36 s (4 893 marchés, 33 marchés de sport demandés par les voisines, un ordre posé).

- **29/10/2026, mise en production et verrouillage** :
  - PR #1 fusionnée à 09:47 ; premier cycle du nouveau code à 09:48 (code 0 : 4 895 marchés, 39 demandés par les
    voisines, mise × 1,0) ; les deux journaux fantômes (`ledger/fantome/`) supprimés de la branche `data` ;
  - la PR #1 a été fusionnée par `automerge` avant la fin de `tests` (aucune règle sur `main` à ce moment-là ;
    `guard` et `tests` étaient verts) ; Etienne a alors créé le ruleset `protection-main` (`guard` et `tests` exigés)
    et coché « Allow auto-merge » : les PR de Claude attendent désormais les deux contrôles avant de se fusionner.
