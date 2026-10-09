#!/usr/bin/env bash
# Fetch the dictionary + word lists the miner needs. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data

echo "Fetching CC-CEDICT..."
curl -sSL -o data/cedict.txt.gz \
  "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
gunzip -kf data/cedict.txt.gz

echo "Fetching HSK word lists..."
for L in 1 2 3 4 5 6; do
  curl -sSL -o "data/hsk_L$L.txt" \
    "https://raw.githubusercontent.com/glxxyz/hskhsk.com/main/data/lists/HSK%20Official%20With%20Definitions%202012%20L$L.txt"
done

echo "Done."
wc -l data/cedict.txt data/hsk_L*.txt
