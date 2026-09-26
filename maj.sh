#!/usr/bin/env sh
set -e
cd "$(dirname "$0")"
echo "1/3  tarifs du jour (CWaPE et Brugel)"
python3 collecte/comparateurs.py tarifs toutes
echo "2/3  reconstruction et controle"
python3 scripts/construire.py | head -3
python3 scripts/verifier.py | head -8
echo "3/3  publication"
git add data collecte/data
if git diff --staged --quiet; then
  echo "rien de neuf"
else
  git commit -m "comparateurs: releve manuel du $(date -u +%F)"
  git push
  echo "pousse sur origin"
fi
