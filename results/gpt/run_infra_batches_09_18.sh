#!/usr/bin/env bash
set -u
ROOT=/home/paulkinlan/state-of-the-web
RESULTS=$ROOT/results/gpt
cd "$RESULTS"
for b in 09 10 11 12 13 14 15 16 17 18; do
  batch="batch-$b"
  sites="$ROOT/scripts/${batch}-sites.tsv"
  echo "[$(date -Is)] START $batch sites=$sites" | tee -a infra-batches-09-18.log
  if [ ! -f "$sites" ]; then
    echo "[$(date -Is)] MISSING $sites" | tee -a infra-batches-09-18.log
    exit 1
  fi
  ./run_gpt_batch.py "$batch" "$sites" > "${batch}.log" 2>&1
  status=$?
  echo "[$(date -Is)] END $batch status=$status" | tee -a infra-batches-09-18.log
  if [ $status -ne 0 ]; then
    echo "[$(date -Is)] ABORT after $batch" | tee -a infra-batches-09-18.log
    exit $status
  fi
  if [ -f "${batch}.json" ]; then
    echo "[$(date -Is)] WROTE ${batch}.json count=$(jq length "${batch}.json" 2>/dev/null || echo '?')" | tee -a infra-batches-09-18.log
  else
    echo "[$(date -Is)] WARNING no ${batch}.json" | tee -a infra-batches-09-18.log
  fi
done
echo "[$(date -Is)] ALL DONE batch-09..batch-18" | tee -a infra-batches-09-18.log
