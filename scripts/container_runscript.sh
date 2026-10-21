#!/bin/bash
set -eu

config="./config.json"

config_set=false
for arg in "$@"; do
    case "$arg" in
        --config|--config=*) config_set=true ;;
    esac
done

case "${1:-}" in
    --config|--config=*) ;;
    -*)
        if [ "$config_set" = false ]; then
            set -- --config "$config" "$@"
        fi
        ;;
    "")
        set -- --config "$config"
        ;;
    *)
        config="$1"
        shift
        set -- --config "$config" "$@"
        ;;
esac

detector_dir="$(
    for setup in /opt/detector/epic-[0-9]*/bin/thisepic.sh; do
        [ -f "$setup" ] || continue
        printf '%s\n' "${setup%/bin/thisepic.sh}"
    done | sort -V | tail -n 1
)"
if [ -z "$detector_dir" ]; then
    echo "ERROR: no versioned EPIC detector setup found under /opt/detector." >&2
    exit 1
fi
echo "Using latest bundled detector: $detector_dir" >&2

exec /bin/bash -lc \
    'source "$1/bin/thisepic.sh" || exit $?; : "${DETECTOR_PATH:?detector setup did not set DETECTOR_PATH}"; export DETECTOR_CACHE="$DETECTOR_PATH"; shift; exec python3 /opt/geant-generator/run_simulations.py "$@"' \
    _ "$detector_dir" "$@"
