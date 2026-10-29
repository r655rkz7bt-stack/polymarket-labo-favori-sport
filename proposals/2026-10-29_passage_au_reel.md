# Proposition : passer `nf_sport_patiente_prix_850_875` à l'argent réel

Date : 29/10/2026. Demandée par Etienne (`CLAUDE.md`, interdit n°1 modifié le 29/10/2026 : le réel est permis
quand Etienne le décide, avec les étapes qu'il choisit).
**Rien de réel n'est branché par cette proposition.** Elle décrit le chemin. Etienne décide de chaque étape, et
les fichiers protégés ne sont modifiés que par lui.

## 1. Le principe : seule l'exécution change

D'après `CLAUDE.md` : « la stratégie reste la même ; simplement modifier les éléments pour placer les ordres ».

- **Inchangé** : la liste des marchés (scanner et voisines), la stratégie, la règle de mise du fantôme
  (`sante.py`), la relecture du carnet, les deux camps, les ordres posés et le cycle de 5 minutes.
- **Change** : l'endroit où l'ordre décidé est envoyé. Aujourd'hui, `execution_simulator.py` le simule contre le
  vrai carnet. En réel, un nouveau module `execution_reelle.py` l'envoie à Polymarket, puis relit ce qui a
  vraiment été exécuté (prix, quantité, frais). Seul ce résultat réel est inscrit au journal signé.
- **Proposé (à décider)** : garder la simulation en parallèle, en « ombre ». Même décision, même instant, prix
  simulé contre prix réel. On mesure ainsi ce que la simulation embellit (glissement, ordres non servis).

## 2. Blocage n°1 : la machine qui envoie les ordres

Faits, d'après la documentation officielle de Polymarket (page « geoblock », lue le 29/10/2026) :

- Contrôle officiel : `GET https://polymarket.com/api/geoblock` renvoie le pays de l'appelant et `blocked`.
- **États-Unis : « close-only »**, c'est-à-dire qu'on peut fermer ses positions mais pas en ouvrir, que ce soit
  sur le site ou par l'API. La France, l'Allemagne, l'Italie, le Royaume-Uni, la Belgique, la Pologne, etc. sont
  aussi « close-only ».
- **L'Égypte n'est dans aucune liste**, donc rien n'y est restreint d'après cette page.
- Les serveurs de Polymarket sont à Londres (eu-west-2).

**Le problème** : les cycles tournent sur les machines de GitHub Actions, qui sont en général aux États-Unis.
Depuis là, Polymarket refuserait d'ouvrir des positions. Je ne contournerai pas ce blocage avec un VPN ou un
proxy : leurs conditions l'interdisent et le compte risquerait d'être bloqué avec les fonds dessus.

**Étape 1 (déjà prête, sans argent ni secret)** : le workflow libre `labo-geoblock` interroge le contrôle
officiel depuis une machine GitHub et affiche le pays vu par Polymarket. Il ne lit aucun secret et ne passe
aucun ordre.

**Options pour la machine (à choisir par Etienne)** :

| Option | Coût | Pour | Contre |
|---|---|---|---|
| A. Un ordinateur ou un vieux téléphone chez Etienne en Égypte, branché en « runner » GitHub | 0 € | ordres depuis le pays du compte, le plus propre | doit rester allumé et connecté en permanence |
| B. Une machine virtuelle gratuite (offre « toujours gratuite » d'un hébergeur) dans un pays non restreint, branchée en « runner » GitHub | 0 € (carte bancaire parfois demandée pour s'inscrire) | toujours allumée | serveur hors d'Égypte : Etienne doit vérifier dans les conditions de Polymarket que c'est permis pour son compte ; doit être un service gratuit (interdit n°2) |
| C. Rester sur GitHub Actions | 0 € | rien à installer | impossible si l'étape 1 montre les États-Unis |

Dans les options A et B, les cycles tournent sur cette machine (`runs-on: self-hosted` dans `cycle.yml`,
fichier protégé). Le reste du système ne change pas.

## 3. Compte, portefeuille et clé

- **Client officiel** : `polymarket-client` (SDK Python officiel de Polymarket, licence MIT, version 0.14.0,
  Python 3.11 et plus). L'ancien `py-clob-client` est archivé et déclaré « plus fonctionnel » par Polymarket : on
  ne l'utilise pas. Le SDK est en 0.x, avec des changements possibles d'une version à l'autre, donc on épingle
  la version exacte.
- **Clé** : le SDK signe les ordres avec la clé privée du portefeuille. Les fonctions utiles sont
  `SecureClient.create(private_key=…, wallet=…)`, `place_limit_order` (avec `expiration` pour les ordres à durée
  limitée), `create_limit_order` (signe sans envoyer), `list_open_orders`, `cancel_order` / `cancel_all` et
  `get_balance_allowance` (solde réel).
- **Recommandation** : un compte ou portefeuille dédié, qui ne contient que le montant qu'Etienne accepte de
  perdre entièrement.
- **Secrets** (entrés par Etienne lui-même, jamais dans une conversation) dans l'environnement `production` :
  `POLYMARKET_PRIVATE_KEY` et `POLYMARKET_WALLET`. Ils ne sont passés qu'à l'étape « Cycle » de `cycle.yml`,
  et aucune ligne de journal ne les affiche.
- **À vérifier pendant l'étape « à blanc » (non vérifié à ce jour)** :
  - le type de portefeuille du compte d'Etienne (compte créé sur le site ou portefeuille externe) et les
    autorisations de dépense qu'il demande ;
  - la monnaie de règlement exacte ;
  - la récupération des gains d'un marché réglé, qui demande peut-être une action « redeem ».

## 4. Règle n°1 en argent réel

La règle n°1 (`ledger.py`, `safety_controller.py`) est gardée telle quelle et s'applique au journal signé. Pour
qu'elle protège de l'argent réel :

1. **Capital de départ** : `config/risk_limits.yaml` → `initial_capital:` = le montant réel décidé par Etienne
   (en dollars, la monnaie de Polymarket). Puis `reset-capital`, avec son code.
2. **Journal = réalité** : chaque achat inscrit au journal vient d'une exécution confirmée par Polymarket (prix
   moyen, quantité et frais réels), jamais d'une simulation.
3. **Contrôle du vrai solde à chaque cycle** : le solde réel lu chez Polymarket est comparé au solde du journal.
   - Écart de plus de 2 % (ou de plus de 5 $) : on ne passe aucun nouvel ordre ce cycle, et un message est écrit.
   - Solde réel ≤ 0 : faillite (verrou de faillite), comme aujourd'hui.
4. **Bouton d'arrêt** : une variable GitHub (pas un secret) `TRADING_REEL` = `marche` / `arret`.
   - `arret` : aucun nouvel ordre et annulation de tous les ordres posés au cycle suivant.
   - Etienne la change dans **Settings → Secrets and variables → Actions → Variables**, sans toucher au code.

## 5. Changements du noyau protégé (Etienne seul, au moment voulu)

| Fichier | Changement proposé |
|---|---|
| `src/polymarket_agent/safety_controller.py` | `assert_simulation_mode` accepte aussi `"reel"`, et seulement si la variable `TRADING_REEL` vaut `marche` ; ajout de la vérification du vrai solde (§4.3) |
| `src/polymarket_agent/__init__.py` | Description : « trading simulé, puis réel sur décision d'Etienne » |
| `config/risk_limits.yaml` | `initial_capital` = montant réel ; commentaire « argent réel » |
| `.github/workflows/cycle.yml` | `runs-on` selon l'option du §2 ; secrets `POLYMARKET_*` et variable `TRADING_REEL` passés à la seule étape « Cycle » ; installation de `polymarket-client` à version épinglée |
| `tests/test_safety_controller.py` | Tests du mode `reel` (refusé sans `marche`, arrêt si écart de solde, faillite si solde réel ≤ 0) |
| `scripts/audit_fills.py` | En réel : compare le journal à l'historique des exécutions de Polymarket (au lieu de rejouer la simulation) |

`execution_simulator.py` et `fees.py` ne changent pas : ils servent à l'« ombre » et à l'estimation des frais
avant l'envoi.

Je préparerai le texte exact de chaque fichier, à copier tel quel, au moment de l'étape 6.

## 6. Les étapes (Etienne décide de l'ordre et du rythme)

1. **Test « pays »** (prêt) : Etienne lance **Actions → labo-geoblock → Run workflow**. On lit le pays affiché.
2. **Choix de la machine** (§2) et mise en place, si besoin, d'un « runner » GitHub, une étape à la fois.
3. **Code « à blanc »** (Claude, partie libre) : `execution_reelle.py` avec trois modes.
   - `simulation` : l'actuel.
   - `a_blanc` : construit et signe les vrais ordres, sans jamais les envoyer, et journalise ce qui aurait été
     envoyé.
   - `reel` : envoie les ordres.

   Ce code s'accompagne de tests avec une fausse API, sans clé réelle. Il gère :
   - pas d'ordre en double si un cycle est relancé (identifiant de chaque ordre gardé, relecture des ordres
     ouverts) ;
   - les ordres partiellement servis ;
   - les annulations à expiration ;
   - le règlement des marchés.
4. **Compte et secrets** (Etienne) : portefeuille dédié, petit dépôt, secrets `POLYMARKET_*`.
5. **Noyau** (Etienne) : modifications du §5, appliquées par lui.
6. **À blanc en production pendant quelques jours** : vrais ordres signés mais pas envoyés, comparés à la
   simulation. On vérifie les points du §3 restés ouverts.
7. **Réel avec un petit montant**, choisi par Etienne. On compare chaque jour les prix réels aux prix simulés.
8. **Montant décidé par Etienne**, seulement s'il le juge bon.

## 7. Ce qu'il faut savoir avant (honnêteté)

- **La preuve est encore mince.**
  - 85 paris simulés et +484 € virtuels.
  - Confiance par jour : 78 %, sous le seuil de 80 % que le labo utilise lui-même pour renforcer la mise.
  - Avance des prévisions sur le prix : +0,0019, c'est-à-dire faible.
- **La mise est en Kelly entier**, sans plafond de risque autre que 25 % du capital par pari. C'est un choix de
  la stratégie, qui reste le même en réel d'après `CLAUDE.md`. Avec de l'argent réel, une mauvaise série peut
  enlever une grosse part du capital en quelques jours. Seul le montant de départ protège : il doit être un
  montant qu'Etienne accepte de perdre en entier.
- **Le réel est en général moins bon que la simulation** : glissement de prix, ordres posés non servis ou servis
  quand c'est défavorable. Une avance faible peut disparaître. L'« ombre » (§1) sert à le mesurer.
- **Risques techniques** :
  - SDK en 0.x, donc changements possibles ;
  - panne de la machine ;
  - clé privée sur une machine qui tourne seule : un portefeuille dédié limite les dégâts possibles.
- **Règles et impôts** : Etienne vérifie les conditions d'utilisation de Polymarket pour son compte et sa
  situation en Égypte.

## 8. Rappel : `CLAUDE.md` et le ruleset

La modification de `CLAUDE.md` (PR #3) a été fusionnée alors que `guard` était rouge. Le ruleset
`protection-main` a donc été mis en pause ou contourné. Il doit être remis sur **Active** :
**Settings → Rules → Rulesets → protection-main → Enforcement status : Active → Save changes**.
Sans lui, une pull request qui touche le noyau ou casse les tests pourrait être fusionnée.
