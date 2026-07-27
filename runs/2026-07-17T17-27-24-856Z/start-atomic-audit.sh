#!/usr/bin/env bash
set -uo pipefail
RUN=/home/paulkinlan/state-of-the-web/runs/2026-07-17T17-27-24-856Z
WAIT_LOG="$RUN/atomic-audit-wait.log"
RETRY_URLS="$RUN/atomic-retry-urls.txt"
RETRY_STATUS="$RUN/atomic-retry-status.json"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"

if ! python3 /home/paulkinlan/state-of-the-web/scripts/build_atomic_retry_queue.py \
  "$RUN" --max-attempts "$MAX_ATTEMPTS" --queue "$RETRY_URLS" --status "$RETRY_STATUS" \
  | tee -a "$WAIT_LOG"; then
  echo "$(date -u +%FT%TZ) retry queue generation failed; refusing to run" >> "$WAIT_LOG"
  exit 2
fi
retry_count="$(python3 - "$RETRY_STATUS" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['queueCount'])
PY
)"
if [ "$retry_count" -eq 0 ]; then
  python3 - "$RUN/run.json" "$RETRY_STATUS" <<'PY'
import json,sys
from datetime import datetime,timezone
run_path,status_path=sys.argv[1:]
d=json.load(open(run_path)); status=json.load(open(status_path))
d['status']='bounded-retries-complete'
d['finishedAt']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
d['retryDisposition']={'path':status_path,'maxAttempts':status['maxAttempts'],'counts':status['counts']}
open(run_path,'w').write(json.dumps(d,indent=2)+'\n')
PY
  echo "$(date -u +%FT%TZ) no retry-eligible sites remain; terminal blocked/partial states retained" >> "$WAIT_LOG"
  exit 0
fi

echo "$(date -u +%FT%TZ) $retry_count sites eligible under max-attempts=$MAX_ATTEMPTS; waiting for openai-codex/gpt-5.6-sol" >> "$WAIT_LOG"
while true; do
  probe="$(cd /tmp && pi -p --no-session --provider openai-codex --model gpt-5.6-sol --no-tools 'Reply with exactly READY.' 2>&1)"
  status=$?
  if [ "$status" -eq 0 ] && printf '%s\n' "$probe" | grep -qx 'READY'; then
    echo "$(date -u +%FT%TZ) OpenAI agent available; starting CrUX top-1,000 atomic run" >> "$WAIT_LOG"
    break
  fi
  echo "$(date -u +%FT%TZ) still waiting: $(printf '%s' "$probe" | tr '\n' ' ' | cut -c1-240)" >> "$WAIT_LOG"
  sleep 15
done
python3 - "$RUN/run.json" <<'PY'
import json,sys
from datetime import datetime,timezone
p=sys.argv[1];d=json.load(open(p));d['status']='running';d['startedAt']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');open(p,'w').write(json.dumps(d,indent=2)+'\n')
PY
cd /home/paulkinlan/web-uplift
exec node bin/web-uplift.mjs audit \
  --agent pi \
  --concurrency 1 \
  --resume \
  --urls "$RETRY_URLS" \
  --out "$RUN/atomic-reports" \
  --verbose 2>&1 | tee -a "$RUN/atomic-audit.log"
