#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# fdh-voice entrypoint: the backend gateway on 127.0.0.1:8790 (it spawns one Hermes worker per
# session), then the voice server on 7860 once the gateway is healthy. If either process exits, the
# other is stopped and the container exits with that status. No `set -e`: a non-zero `wait -n` must
# still reach stop().
set -uo pipefail
cd /app
mkdir -p logs
export PYTHONPATH=/app/src FDH_HERMES_PYTHON=/app/.venv/bin/python
CFG=src/prototypes/voice_delegation_hermes_agent/config
gw="" voice=""

stop() {
  [ -n "$voice" ] && kill -TERM "$voice" 2>/dev/null
  [ -n "$gw" ] && kill -TERM "$gw" 2>/dev/null
  [ -n "$voice" ] && wait "$voice"
  [ -n "$gw" ] && wait "$gw"
}
trap 'stop; exit 143' TERM INT

FDH_GATEWAY_HOST=127.0.0.1 FDH_GATEWAY_PORT=8790 \
  python -m prototypes.voice_delegation_hermes_agent.sidecar.gateway_server \
    --config "$CFG/${FDH_GATEWAY_CONFIG:-gateway.eval.yaml}" --max-sessions "${FDH_GATEWAY_MAX_SESSIONS:-8}" &
gw=$!
ready=0
for _ in $(seq 60); do
  curl -fs -m 2 http://127.0.0.1:8790/health >/dev/null && { ready=1; break; }
  kill -0 "$gw" 2>/dev/null || break
  sleep 0.5
done
[ "$ready" = 1 ] || { echo "gateway not healthy" >&2; stop; exit 1; }

python -m prototypes.voice_delegation_hermes_agent.server \
  --config "$CFG/profiles/${FDH_PROFILE:-realtime_eval.yaml}" --port 7860 &
voice=$!

rc=0
wait -n "$gw" "$voice" || rc=$?
stop
exit "$rc"
