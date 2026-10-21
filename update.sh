#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/.build"
BASE_IMAGE="$BUILD_DIR/eic_xl-nightly.sif"
OUTPUT_DIR="$ROOT_DIR/singularity"
TMP_DIR="${APPTAINER_TMPDIR:-${SINGULARITY_TMPDIR:-$BUILD_DIR/tmp}}"
CLEAN_TMP_DIR=false
if [[ -z "${APPTAINER_TMPDIR:-}" && -z "${SINGULARITY_TMPDIR:-}" ]]; then
    CLEAN_TMP_DIR=true
fi
RESET_CONFIG=false
LOCAL_BASE=""

usage() {
    cat <<'EOF'
Build/update the EIC Geant generator container.

Usage:
  ./update.sh [--base-sif PATH] [--reset-config]

By default, pulls the latest EIC XL nightly base image from
docker://eicweb/eic_xl:nightly, then builds the generator SIF.

Options:
  --base-sif PATH   Use an existing local EIC SIF instead of pulling one.
                    Useful for offline builds and testing.
  --reset-config    Replace singularity/config.json with scripts/config.json.
                    Without this, an existing config is preserved.
  -h, --help        Show this help.
EOF
}

while (($#)); do
    case "$1" in
        --base-sif)
            if (($# < 2)); then
                echo "ERROR: --base-sif requires a path." >&2
                exit 2
            fi
            LOCAL_BASE="$2"
            shift 2
            ;;
        --reset-config)
            RESET_CONFIG=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

run_privileged() {
    if ((EUID == 0)); then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "ERROR: installing Apptainer requires root or sudo." >&2
        return 1
    fi
}

if ! command -v apptainer >/dev/null 2>&1 \
    && ! command -v singularity >/dev/null 2>&1; then
    echo "Apptainer/Singularity not found; attempting to install Apptainer..."
    if command -v apt-get >/dev/null 2>&1; then
        run_privileged apt-get update
        run_privileged apt-get install -y apptainer
    elif command -v dnf >/dev/null 2>&1; then
        run_privileged dnf install -y apptainer
    elif command -v yum >/dev/null 2>&1; then
        run_privileged yum install -y apptainer
    elif command -v zypper >/dev/null 2>&1; then
        run_privileged zypper --non-interactive install apptainer
    elif command -v pacman >/dev/null 2>&1; then
        run_privileged pacman --noconfirm -S apptainer
    else
        echo "ERROR: no supported package manager found. Install Apptainer or Singularity, then rerun." >&2
        exit 1
    fi
fi

if command -v apptainer >/dev/null 2>&1; then
    CONTAINER_RUNTIME="$(command -v apptainer)"
elif command -v singularity >/dev/null 2>&1; then
    CONTAINER_RUNTIME="$(command -v singularity)"
else
    echo "ERROR: Apptainer installation did not provide an apptainer command." >&2
    exit 1
fi

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR/output"
mkdir -p "$TMP_DIR"
export TMPDIR="$TMP_DIR"
export APPTAINER_TMPDIR="$TMP_DIR"
export SINGULARITY_TMPDIR="$TMP_DIR"

cleanup_tmp_dir() {
    local exit_status=$?
    if [[ "$CLEAN_TMP_DIR" == true && "$exit_status" -eq 0 ]]; then
        rmdir -- "$TMP_DIR"
    fi
}
trap cleanup_tmp_dir EXIT

if [[ -n "$LOCAL_BASE" ]]; then
    LOCAL_BASE="$(realpath -- "$LOCAL_BASE")"
    if [[ ! -f "$LOCAL_BASE" ]]; then
        echo "ERROR: base SIF not found: $LOCAL_BASE" >&2
        exit 1
    fi
    ln -sfn -- "$LOCAL_BASE" "$BASE_IMAGE"
else
    echo "Pulling latest EIC XL nightly base image..."
    "$CONTAINER_RUNTIME" pull --force "$BASE_IMAGE" docker://eicweb/eic_xl:nightly
fi

if [[ ! -f "$BASE_IMAGE" ]]; then
    echo "ERROR: EIC base image was not created: $BASE_IMAGE" >&2
    exit 1
fi

if [[ "$RESET_CONFIG" == true || ! -f "$OUTPUT_DIR/config.json" ]]; then
    cp -- "$ROOT_DIR/scripts/config.json" "$OUTPUT_DIR/config.json"
else
    echo "Preserving existing config: $OUTPUT_DIR/config.json"
fi

echo "Building generator image with $CONTAINER_RUNTIME..."
cd "$ROOT_DIR"
if ((EUID == 0)); then
    "$CONTAINER_RUNTIME" build --force "$OUTPUT_DIR/geant-generator.sif" Singularity.def
else
    echo "Trying an unprivileged fakeroot build..."
    if "$CONTAINER_RUNTIME" build --fakeroot --force \
        "$OUTPUT_DIR/geant-generator.sif" Singularity.def; then
        :
    else
        echo "Fakeroot build failed or is unavailable; falling back to sudo."
        if ! command -v sudo >/dev/null 2>&1; then
            echo "ERROR: fakeroot build failed and sudo is not installed." >&2
            exit 1
        fi
        rm -f -- "$OUTPUT_DIR/geant-generator.sif"
        sudo env TMPDIR="$TMPDIR" \
            APPTAINER_TMPDIR="$APPTAINER_TMPDIR" \
            SINGULARITY_TMPDIR="$SINGULARITY_TMPDIR" \
            "$CONTAINER_RUNTIME" build --force \
            "$OUTPUT_DIR/geant-generator.sif" Singularity.def
    fi
fi
chmod +x "$OUTPUT_DIR/geant-generator.sif"

echo "Built: $OUTPUT_DIR/geant-generator.sif"
echo "Config: $OUTPUT_DIR/config.json"
echo "Run: cd \"$OUTPUT_DIR\" && ./geant-generator.sif --dry-run"
