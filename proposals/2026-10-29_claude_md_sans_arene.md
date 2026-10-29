# Proposition : mettre `CLAUDE.md` à jour (plus d'arène)

`CLAUDE.md` est dans le noyau protégé : seul Etienne peut le modifier. Depuis le 29/10/2026, il n'y a plus d'arène ni de
portefeuilles fantômes (docs/CONTEXTE.md §6), mais la section « Mission » de `CLAUDE.md` dit encore :

> But : faire tourner cette stratégie à part et y tester des variantes (réglages, filtres, mises, exécution), chacune
> dans son propre portefeuille fantôme de l'arène, avec ses indices de confiance.

et, dans « Liberté » : « … (variantes de la stratégie, cycle, arène et ses règles, tableau de bord, scripts, … ».

## Texte proposé

Remplacer la phrase « But : … » par :

> But : faire tourner cette stratégie directement sur le capital principal (plus d'arène depuis le 29/10/2026) et
> suivre ses indices de confiance ; un changement de réglage (filtres, mises, exécution) s'applique à ce capital et sa
> date est notée dans `docs/CONTEXTE.md`.

et, dans « Liberté », remplacer « arène et ses règles » par « réglages et plafonds du capital principal ».

## Comment faire (Etienne)

Tant que le ruleset `protection-main` n'existe pas, il suffit de modifier le fichier sur GitHub :
1. Onglet **Code** → `CLAUDE.md` → icône **crayon** (Edit this file).
2. Remplacer les deux passages ci-dessus, puis **Commit changes…** → **Commit changes**.
3. Vérification : la page de `CLAUDE.md` montre le nouveau texte.

Rien ne presse : l'ancien texte ne bloque rien, il est seulement inexact.
