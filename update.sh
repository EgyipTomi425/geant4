#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/.build"
BASE_IMAGE="$BUILD_DIR/eic_xl-nightly.sif"
OUTPUT_DIR="$ROOT_DIR/apptainer"
TMP_DIR_PARENT="${APPTAINER_TMPDIR:-$BUILD_DIR/tmp}"
CLEAN_TMP_PARENT=false
if [[ -z "${APPTAINER_TMPDIR:-}" ]]; then
    CLEAN_TMP_PARENT=true
fi
TMP_DIR=""
BUILD_IMAGE="$OUTPUT_DIR/.geant-generator.sif.tmp.$$"
RESET_CONFIG=false
LOCAL_BASE=""
APPTAINER_VERSION="1.5.4"
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
  --reset-config    Replace apptainer/config.json with scripts/config.json.
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

print_green_block() {
    if [[ -t 2 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]]; then
        printf '\033[1;32m' >&2
    fi
    cat >&2
    if [[ -t 2 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]]; then
        printf '\033[0m' >&2
    fi
}

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR/output" "$TMP_DIR_PARENT"
TMP_DIR="$(mktemp -d "$TMP_DIR_PARENT/geant-generator.XXXXXX")"
export TMPDIR="$TMP_DIR"
export APPTAINER_TMPDIR="$TMP_DIR"

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

install_apptainer() {
    if ! command -v apt-get >/dev/null 2>&1 \
        || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
        echo "ERROR: automatic Apptainer installation supports Debian/Ubuntu only. Install Apptainer and rerun." >&2
        return 1
    fi

    echo "Apptainer not found; installing its build dependencies..."
    run_privileged apt-get update
    local packages=(
        autoconf automake build-essential cryptsetup curl dh-apparmor fakeroot
        git libattr1-dev libfuse3-dev liblz4-dev liblzma-dev liblzo2-dev
        libprotobuf-c-dev libseccomp-dev libtalloc-dev libtool libzstd-dev
        pkg-config proot squashfs-tools tzdata uidmap wget zlib1g-dev
    )
    local os_id os_version
    . /etc/os-release
    os_id="$ID"
    os_version="$VERSION_ID"
    if [[ "$os_id" == ubuntu ]] \
        && dpkg --compare-versions "$os_version" ge 24.04; then
        packages+=(libsubid-dev)
    elif [[ "$os_id" == debian ]] \
        && dpkg --compare-versions "$os_version" ge 12; then
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

    local source_dir="$TMP_DIR/apptainer-install"
    local apptainer_src="$source_dir/apptainer-$APPTAINER_VERSION"
    mkdir -p "$source_dir"
    wget -O "$source_dir/go.tar.gz" \
        "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz"
    tar -xzf "$source_dir/go.tar.gz" -C "$source_dir"
    wget -O "$source_dir/apptainer.tar.gz" \
        "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer-${APPTAINER_VERSION}.tar.gz"
    tar -xzf "$source_dir/apptainer.tar.gz" -C "$source_dir"

    (
        cd "$apptainer_src"
        export PATH="$source_dir/go/bin:$PATH"
        ./mconfig
        ./scripts/download-dependencies
        ./scripts/compile-dependencies
        run_privileged ./scripts/install-dependencies
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
    echo "Trying an unprivileged Apptainer build with proot..."
    if "$CONTAINER_RUNTIME" build --force "$BUILD_IMAGE" Apptainer.def; then
        return 0
    fi
    return 1
}

report_local_build_failure() {
    print_green_block <<'EOF'

ERROR: all local Apptainer build methods failed.
If the errors above mention user namespaces or "Operation not permitted",
this host/container blocks kernel operations required for the build. Sudo
inside a container cannot override that host restriction.

Run this update on the Linux host, or ask the host administrator to enable
Apptainer build/user-namespace support for this container.
The previous working generator image, if any, has been preserved.
EOF
}

if ! command -v apptainer >/dev/null 2>&1; then
    install_apptainer
fi
if ! command -v apptainer >/dev/null 2>&1; then
    echo "ERROR: Apptainer installation did not provide an apptainer command." >&2
    exit 1
fi
CONTAINER_RUNTIME="$(command -v apptainer)"

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
    if ! "$CONTAINER_RUNTIME" build --force "$BUILD_IMAGE" Apptainer.def; then
        echo "Privileged local build failed; trying an unprivileged proot build."
        if ! try_proot_build; then
            echo "Local proot build failed."
            report_local_build_failure
            exit 1
        fi
    fi
else
    echo "Trying an unprivileged fakeroot build..."
    if "$CONTAINER_RUNTIME" build --fakeroot --force \
        "$BUILD_IMAGE" Apptainer.def; then
        :
    else
        echo "Fakeroot build failed or is unavailable."
        sudo_build_succeeded=false
        if command -v sudo >/dev/null 2>&1; then
            echo "Falling back to a sudo build..."
            rm -f -- "$BUILD_IMAGE"
            if sudo env TMPDIR="$TMPDIR" \
                APPTAINER_TMPDIR="$APPTAINER_TMPDIR" \
                "$CONTAINER_RUNTIME" build --force \
                "$BUILD_IMAGE" Apptainer.def; then
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
                echo "Local proot build failed."
                report_local_build_failure
                exit 1
            fi
        fi
    fi
fi
chmod +x "$BUILD_IMAGE"
mv -f -- "$BUILD_IMAGE" "$OUTPUT_DIR/geant-generator.sif"

echo "Built: $OUTPUT_DIR/geant-generator.sif"
echo "Config: $OUTPUT_DIR/config.json"
echo "Run: cd \"$OUTPUT_DIR\" && ./geant-generator.sif --dry-run"
