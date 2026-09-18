#!/usr/bin/env bash
# Full z0int spine E2E: open → close(measured) → summary → L2 → outer → kerdoios
set -euo pipefail
SESSION="${1:-live-e2e}"
PROMPT="${2:-e2e spine: edit recovery path and verify}"
Z0_ROOT="${Z0INT_ROOT:-/home/kvn/tmp/openjev}"
EL_ROOT="${EVOLUTION_LAB_ROOT:-/workspace/evolution-lab}"
BRIDGE="$Z0_ROOT/omp-extensions/z0int-bridge"
Z0_PY="${Z0INT_PYTHON:-$Z0_ROOT/.venv/bin/python}"
EL_PY="${EVOLUTION_LAB_PYTHON:-$EL_ROOT/.venv/bin/python}"
KERD="${KERDOIOS_ROOT:-$HOME/.hermes/profiles/chiefstaff/plugins/kerdoios}"

cd "$BRIDGE"
bun -e "
import { turnBridge, closeOpenTurn } from './index.ts';
const row = await turnBridge(process.env.PROMPT || '$PROMPT', process.env.SESSION || '$SESSION');
const base = (Number(row.receipt?.baseline_input_tokens||0)+Number(row.receipt?.baseline_output_tokens||0));
const meas = base>0 ? Math.round(base*0.4) : 800;
const closed = await closeOpenTurn({
  traceId: row.trace_id,
  measured: meas,
  inputTokens: Math.round(meas*0.75),
  outputTokens: Math.round(meas*0.25),
  success: true, testPass: true, toolOk: true,
  source: 'e2e_spine_sh',
  provider: 'e2e', model: 'script',
});
console.log(JSON.stringify({trace:row.trace_id, session:row.session_id, saved:closed.actual_tokens_saved, measured:closed.measured_frontier_tokens, tier:(closed.outcome_join||{}).outcome_tier}, null, 2));
"

echo '--- summary ---'
"$Z0_PY" -m z0int receipt summary --json
echo '--- L2 ---'
"$EL_PY" -m evolution_lab l2-recovery --closed-loop-seeds 4 | "$EL_PY" -c 'import sys,json;d=json.load(sys.stdin);print({"L2":d["gates"]["pass"],"canary":d.get("canary_marker")})'
echo '--- outer ---'
"$EL_PY" -m evolution_lab outer-loop --skip-mine --closed-loop-seeds 4 | "$EL_PY" -c 'import sys,json;d=json.load(sys.stdin);print({"outer":d["steps"]["l2_recovery"]["ok"]})'
echo '--- kerdoios ---'
python3 -m kerdoios economics 2>/dev/null || (cd "$KERD" && python3 -m kerdoios economics)
