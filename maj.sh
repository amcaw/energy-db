#!/usr/bin/env sh
set -e
BAROMETRE="${1:-../svelte_energy/static/donnees/barometre.json}"
cd "$(dirname "$0")"
echo "1/4  import des contrats depuis $BAROMETRE"
python3 scripts/importer_contrats.py "$BAROMETRE"
echo "2/4  reconstruction de la base"
python3 scripts/construire.py | head -3
echo "3/4  controle"
python3 scripts/verifier.py | head -6
echo "4/4  publication"
git add data collecte/data
if git diff --staged --quiet; then
  echo "rien de neuf"
else
  git commit -m "contrats: mise a jour du $(date -u +%F)"
  git push
  echo "pousse sur origin"
fi
