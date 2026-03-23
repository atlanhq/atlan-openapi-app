#!/bin/sh
# Temporary entrypoint — copied from application-sdk/entrypoint.sh (refactor-v3 @ b3861738).
# TODO: remove this file and the COPY/ENTRYPOINT lines in the Dockerfile once the base
# image ghcr.io/atlanhq/application-sdk:latest is updated to include it.
#
# Starts daprd (DAPR runtime) directly alongside the Python application,
# giving full control over graceful shutdown behaviour.

set -eu

export DAPR_APP_ID="${DAPR_APP_ID:-${ATLAN_SERVICE_NAME:-app}}"
export DAPR_APP_PORT="${DAPR_APP_PORT:-8080}"
export DAPR_HTTP_PORT="${DAPR_HTTP_PORT:-3500}"
export DAPR_GRPC_PORT="${DAPR_GRPC_PORT:-50001}"
export DAPR_COMPONENTS_PATH="${DAPR_COMPONENTS_PATH:-/app/components}"
export DAPR_LOG_LEVEL="${DAPR_LOG_LEVEL:-warn}"
export DAPR_METRICS_PORT="${DAPR_METRICS_PORT:-3100}"
export DAPR_MAX_BODY_SIZE="${DAPR_MAX_BODY_SIZE:-1024Mi}"
export DAPR_SCHEDULER_HOST_ADDRESS="${DAPR_SCHEDULER_HOST_ADDRESS:-}"
export DAPR_GRACEFUL_SHUTDOWN_SECONDS="${DAPR_GRACEFUL_SHUTDOWN_SECONDS:-3600}"

DAPRD_PID=""
APP_PID=""

forward_signal() {
    if [ -n "${APP_PID}" ]; then
        echo "[entrypoint] Forwarding SIGTERM to Python app PID ${APP_PID}"
        kill -TERM "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    if [ -n "${DAPRD_PID}" ]; then
        echo "[entrypoint] Python exited, stopping daprd PID ${DAPRD_PID}"
        kill -TERM "${DAPRD_PID}" 2>/dev/null || true
        wait "${DAPRD_PID}" 2>/dev/null || true
    fi
    exit 0
}

trap forward_signal SIGTERM SIGINT

echo "[entrypoint] Starting daprd (app-id=${DAPR_APP_ID}, app-port=${DAPR_APP_PORT})"

daprd \
    --app-id "${DAPR_APP_ID}" \
    --app-port "${DAPR_APP_PORT}" \
    --dapr-http-port "${DAPR_HTTP_PORT}" \
    --dapr-grpc-port "${DAPR_GRPC_PORT}" \
    --resources-path "${DAPR_COMPONENTS_PATH}" \
    --log-level "${DAPR_LOG_LEVEL}" \
    --metrics-port "${DAPR_METRICS_PORT}" \
    --max-body-size "${DAPR_MAX_BODY_SIZE}" \
    --placement-host-address "" \
    --scheduler-host-address "${DAPR_SCHEDULER_HOST_ADDRESS}" \
    --dapr-graceful-shutdown-seconds "${DAPR_GRACEFUL_SHUTDOWN_SECONDS}" &
DAPRD_PID=$!

sleep 0.5
if ! kill -0 "${DAPRD_PID}" 2>/dev/null; then
    echo "[entrypoint] ERROR: daprd exited unexpectedly during startup"
    exit 1
fi

echo "[entrypoint] Starting Python app"
uv run --no-sync python -m application_sdk.main "$@" &
APP_PID=$!

wait "${APP_PID}"
EXIT_CODE=$?

if kill -0 "${DAPRD_PID}" 2>/dev/null; then
    kill -TERM "${DAPRD_PID}" 2>/dev/null || true
    wait "${DAPRD_PID}" 2>/dev/null || true
fi

exit ${EXIT_CODE}
