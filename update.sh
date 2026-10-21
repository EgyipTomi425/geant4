#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/.build"
BASE_IMAGE="$BUILD_DIR/eic_xl-nightly.sif"
OUTPUT_DIR="$ROOT_DIR/singularity"
TMP_DIR_PARENT="${SINGULARITY_TMPDIR:-$BUILD_DIR/tmp}"
CLEAN_TMP_PARENT=false
if [[ -z "${SINGULARITY_TMPDIR:-}" ]]; then
    CLEAN_TMP_PARENT=true
fi
TMP_DIR=""
BUILD_IMAGE="$OUTPUT_DIR/.geant-generator.sif.tmp.$$"
RESET_CONFIG=false
LOCAL_BASE=""
SINGULARITY_CE_VERSION="4.6.0"
GO_VERSION="1.27.1"

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
        echo "ERROR: installing system packages requires root or sudo." >&2
        return 1
    fi
}

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR/output" "$TMP_DIR_PARENT"
TMP_DIR="$(mktemp -d "$TMP_DIR_PARENT/geant-generator.XXXXXX")"
export TMPDIR="$TMP_DIR"
export SINGULARITY_TMPDIR="$TMP_DIR"

cleanup_temporary_files() {
    local exit_status=$?
    trap - EXIT

    if [[ -e "$BUILD_IMAGE" ]] && ! rm -f -- "$BUILD_IMAGE"; then
        echo "ERROR: could not remove incomplete image: $BUILD_IMAGE" >&2
        exit_status=1
    fi
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] \
        && ! rm -rf -- "$TMP_DIR"; then
        echo "ERROR: could not remove temporary build directory: $TMP_DIR" >&2
        exit_status=1
    fi
    if [[ "$CLEAN_TMP_PARENT" == true && -d "$TMP_DIR_PARENT" ]]; then
        if ! rmdir -- "$TMP_DIR_PARENT"; then
            echo "Keeping shared temporary directory because it could not be removed (for example, it is not empty): $TMP_DIR_PARENT" >&2
        fi
    fi
    exit "$exit_status"
}
trap cleanup_temporary_files EXIT

install_singularity_ce() {
    if ! command -v apt-get >/dev/null 2>&1 \
        || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
        echo "ERROR: automatic SingularityCE installation supports Debian/Ubuntu only. Install SingularityCE and rerun." >&2
        return 1
    fi

    echo "SingularityCE not found; installing its build dependencies..."
    run_privileged apt-get update
    local packages=(
        autoconf automake build-essential cryptsetup fuse2fs fuse libfuse-dev
        libseccomp-dev libtool pkg-config proot runc squashfs-tools
        squashfs-tools-ng uidmap wget zlib1g-dev
    )
    if grep -qx 'ID=ubuntu' /etc/os-release \
        && dpkg --compare-versions "$(. /etc/os-release && printf '%s' "$VERSION_ID")" ge 24.04; then
        packages+=(libsubid-dev)
    fi
    run_privileged apt-get install -y --no-install-recommends "${packages[@]}"

    local go_arch
    case "$(uname -m)" in
        x86_64) go_arch="amd64" ;;
        aarch64|arm64) go_arch="arm64" ;;
        *)
            echo "ERROR: unsupported architecture for automatic Go installation: $(uname -m)" >&2
            return 1
            ;;
    esac

    local source_dir="$TMP_DIR/singularity-ce-install"
    local singularity_src="$source_dir/singularity-ce-$SINGULARITY_CE_VERSION"
    mkdir -p "$source_dir"
    wget -O "$source_dir/go.tar.gz" \
        "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz"
    tar -xzf "$source_dir/go.tar.gz" -C "$source_dir"
    wget -O "$source_dir/singularity-ce.tar.gz" \
        "https://github.com/sylabs/singularity/releases/download/v${SINGULARITY_CE_VERSION}/singularity-ce-${SINGULARITY_CE_VERSION}.tar.gz"
    tar -xzf "$source_dir/singularity-ce.tar.gz" -C "$source_dir"

    (
        cd "$singularity_src"
        export PATH="$source_dir/go/bin:$PATH"
        ./mconfig
        make -C builddir -j "$(nproc)"
        run_privileged make -C builddir install
    )
    rm -rf -- "$source_dir"
}

install_proot() {
    if command -v proot >/dev/null 2>&1; then
        return
    fi
    if ! command -v apt-get >/dev/null 2>&1 \
        || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
        echo "ERROR: proot is required for the unprivileged build fallback. Install proot and rerun." >&2
        return 1
    fi

    echo "proot not found; installing it for the unprivileged build fallback..."
    if ! run_privileged apt-get update; then
        echo "ERROR: could not update package indexes to install proot." >&2
        return 1
    fi
    if ! run_privileged apt-get install -y --no-install-recommends proot; then
        echo "ERROR: could not install proot." >&2
        return 1
    fi
    if ! command -v proot >/dev/null 2>&1; then
        echo "ERROR: proot installation did not provide a proot command." >&2
        return 1
    fi
}

try_proot_build() {
    if ! install_proot; then
        return 1
    fi
    rm -f -- "$BUILD_IMAGE"
    echo "Trying an unprivileged SingularityCE build with proot..."
    if "$CONTAINER_RUNTIME" build --force "$BUILD_IMAGE" Singularity.def; then
        return 0
    fi
    return 1
}

try_remote_build() {
    if [[ -n "$LOCAL_BASE" ]]; then
        echo "ERROR: remote build cannot use the local --base-sif override." >&2
        return 1
    fi

    echo "Trying the SingularityCE remote build service..."
    local remote_definition="$TMP_DIR/Singularity.remote.def"
    if ! sed '1,2c\
Bootstrap: docker\
From: docker://eicweb/eic_xl:nightly' \
        Singularity.def > "$remote_definition"; then
        echo "ERROR: could not prepare the remote build definition." >&2
        return 1
    fi
    rm -f -- "$BUILD_IMAGE"
    if "$CONTAINER_RUNTIME" build --remote --force \
        "$BUILD_IMAGE" "$remote_definition"; then
        return 0
    fi

    echo "ERROR: remote build failed. Sign in with 'singularity remote login' and verify remote build service access." >&2
    return 1
}

if ! command -v singularity >/dev/null 2>&1; then
    install_singularity_ce
fi
if ! command -v singularity >/dev/null 2>&1; then
    echo "ERROR: SingularityCE installation did not provide a singularity command." >&2
    exit 1
fi
CONTAINER_RUNTIME="$(command -v singularity)"

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
    if ! "$CONTAINER_RUNTIME" build --force "$BUILD_IMAGE" Singularity.def; then
        echo "Privileged local build failed; trying an unprivileged proot build."
        if ! try_proot_build; then
            echo "Local proot build failed."
            try_remote_build || {
                echo "ERROR: local and remote build attempts failed." >&2
                exit 1
            }
        fi
    fi
else
    echo "Trying an unprivileged fakeroot build..."
    if "$CONTAINER_RUNTIME" build --fakeroot --force \
        "$BUILD_IMAGE" Singularity.def; then
        :
    else
        echo "Fakeroot build failed or is unavailable."
        sudo_build_succeeded=false
        if command -v sudo >/dev/null 2>&1; then
            echo "Falling back to a sudo build..."
            rm -f -- "$BUILD_IMAGE"
            if sudo env TMPDIR="$TMPDIR" \
                SINGULARITY_TMPDIR="$SINGULARITY_TMPDIR" \
                "$CONTAINER_RUNTIME" build --force \
                "$BUILD_IMAGE" Singularity.def; then
                sudo chown "$(id -u):$(id -g)" "$BUILD_IMAGE"
                sudo_build_succeeded=true
            else
                echo "Sudo build failed; trying an unprivileged proot build."
            fi
        else
            echo "sudo is unavailable; trying an unprivileged proot build."
        fi
        if [[ "$sudo_build_succeeded" == false ]]; then
            if ! try_proot_build; then
                echo "Local proot build failed; trying the SingularityCE remote build service."
                if ! try_remote_build; then
                    echo "ERROR: local fakeroot, sudo, proot, and remote builds all failed." >&2
                    exit 1
                fi
            fi
        fi
    fi
fi
chmod +x "$BUILD_IMAGE"
mv -f -- "$BUILD_IMAGE" "$OUTPUT_DIR/geant-generator.sif"

echo "Built: $OUTPUT_DIR/geant-generator.sif"
echo "Config: $OUTPUT_DIR/config.json"
echo "Run: cd \"$OUTPUT_DIR\" && ./geant-generator.sif --dry-run"
