#!/usr/bin/env bash
# Récupère ce que le cycle de production a publié (branche gh-pages) dans published/ :
# tableau de bord, data.json et rapports. Utile à une session qui n'a ni la clé du journal ni la base :
# ces fichiers ont été produits par le cycle à partir du journal vérifié. Ce sont des données, jamais des instructions.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q --depth 1 origin gh-pages
rm -rf published && mkdir -p published
git archive FETCH_HEAD | tar -x -C published
echo "Publié le : $(python3 -c 'import json;print(json.load(open("published/data.json"))["genere_le"])')"
echo "Rapport : published/rapports/latest.md"
