#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/.build"
BASE_IMAGE="$BUILD_DIR/eic_xl-nightly.sif"
OUTPUT_DIR="$ROOT_DIR/apptainer"
LEGACY_OUTPUT_DIR="$ROOT_DIR/singularity"
TMP_DIR_PARENT="${APPTAINER_TMPDIR:-$BUILD_DIR/tmp}"
CLEAN_TMP_PARENT=false
if [[ -z "${APPTAINER_TMPDIR:-}" ]]; then
    CLEAN_TMP_PARENT=true
fi
TMP_DIR=""
BUILD_IMAGE="$OUTPUT_DIR/.geant-generator.sif.tmp.$$"
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

if [[ -d "$LEGACY_OUTPUT_DIR" && ! -e "$OUTPUT_DIR" ]]; then
    mv -- "$LEGACY_OUTPUT_DIR" "$OUTPUT_DIR"
    echo "Migrated existing generated files to $OUTPUT_DIR"
fi

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

inspect_proot_libraries() {
    env -u LD_LIBRARY_PATH -u LD_PRELOAD ldd "$1"
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
    local repair_proot=false target_libexec_dir=""
    if [[ "${1:-}" == "--repair-proot" ]]; then
        repair_proot=true
        target_libexec_dir="${2:-}"
        if [[ -z "$target_libexec_dir" || "$target_libexec_dir" != /* ]]; then
            echo "ERROR: repairing Apptainer's bundled proot requires an absolute LIBEXECDIR." >&2
            return 1
        fi
    fi

    if ! command -v apt-get >/dev/null 2>&1 \
        || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
        echo "ERROR: automatic Apptainer installation supports Debian/Ubuntu only. Install Apptainer and rerun." >&2
        return 1
    fi

    if [[ "$repair_proot" == false ]]; then
        echo "Apptainer not found; checking the Debian/Ubuntu package repositories first..."
    else
        echo "Repairing Apptainer's bundled proot using a clean upstream build..."
    fi
    run_privileged apt-get update
    if [[ "$repair_proot" == false ]]; then
        if run_privileged apt-get install -y --no-install-recommends apptainer; then
            if command -v apptainer >/dev/null 2>&1; then
                echo "Installed Apptainer from the system package repository."
                return
            fi
            echo "WARNING: the package install succeeded but did not provide apptainer; falling back to a source build." >&2
        else
            echo "Apptainer is unavailable from the configured package repositories; falling back to the latest upstream source release."
        fi
    fi

    echo "Installing build dependencies for the upstream Apptainer source build..."
    local packages=(
        autoconf automake build-essential cryptsetup curl dh-apparmor fakeroot
        git libattr1-dev libfuse3-dev liblz4-dev liblzma-dev liblzo2-dev
        libprotobuf-c-dev libseccomp-dev libtalloc-dev libtool libzstd-dev
        pkg-config proot python3 squashfs-tools tzdata uidmap wget zlib1g-dev
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

    local go_arch apptainer_tag apptainer_version go_archive go_version
    case "$(uname -m)" in
        x86_64) go_arch="amd64" ;;
        aarch64|arm64) go_arch="arm64" ;;
        *)
            echo "ERROR: unsupported architecture for automatic Go installation: $(uname -m)" >&2
            return 1
            ;;
    esac

    local source_dir="$TMP_DIR/apptainer-install"
    mkdir -p "$source_dir"
    echo "Looking up the latest stable Apptainer and Go releases..."
    wget -qO "$source_dir/apptainer-release.json" \
        https://api.github.com/repos/apptainer/apptainer/releases/latest
    apptainer_tag="$(/usr/bin/python3 - "$source_dir/apptainer-release.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as release_file:
    print(json.load(release_file)["tag_name"])
PY
)"
    if [[ ! "$apptainer_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "ERROR: unexpected latest Apptainer release tag: $apptainer_tag" >&2
        return 1
    fi
    apptainer_version="${apptainer_tag#v}"
    local apptainer_src="$source_dir/apptainer-$apptainer_version"

    wget -qO "$source_dir/go-releases.json" \
        'https://go.dev/dl/?mode=json'
    go_archive="$(/usr/bin/python3 - "$source_dir/go-releases.json" "$go_arch" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as releases_file:
    releases = json.load(releases_file)

for release in releases:
    if not release.get("stable"):
        continue
    for archive in release.get("files", []):
        if (
            archive.get("os") == "linux"
            and archive.get("arch") == sys.argv[2]
            and archive.get("kind") == "archive"
        ):
            print(archive["filename"])
            raise SystemExit(0)

raise SystemExit("No stable Go archive found for this Linux architecture")
PY
)"
    go_version="${go_archive#go}"
    go_version="${go_version%%.linux-*}"
    echo "Installing Apptainer $apptainer_version and Go $go_version."
    wget -O "$source_dir/$go_archive" "https://go.dev/dl/$go_archive"
    tar -xzf "$source_dir/$go_archive" -C "$source_dir"
    wget -O "$source_dir/apptainer.tar.gz" \
        "https://github.com/apptainer/apptainer/releases/download/${apptainer_tag}/apptainer-${apptainer_version}.tar.gz"
    tar -xzf "$source_dir/apptainer.tar.gz" -C "$source_dir"

    (
        cd "$apptainer_src"
        export PATH="$source_dir/go/bin:/usr/bin:/bin"
        unset CONDA_DEFAULT_ENV CONDA_PREFIX CONDA_PROMPT_MODIFIER
        unset LD_LIBRARY_PATH PYTHONHOME PYTHONPATH VIRTUAL_ENV
        export WITHOUT_PYTHON=1
        if [[ "$repair_proot" == true ]]; then
            ./mconfig --without-suid \
                --prefix="$("$CONTAINER_RUNTIME" buildcfg PREFIX)" \
                --libexecdir="$target_libexec_dir"
        else
            ./mconfig --without-suid
        fi
        ./scripts/download-dependencies
        /usr/bin/python3 - ./scripts/compile-dependencies <<'PY'
import pathlib
import sys

script = pathlib.Path(sys.argv[1])
contents = script.read_text(encoding="utf-8")
needle = '    case "$PKG" in\n'
replacement = r'''    if [ "$PKG" = PRoot ]; then
        /usr/bin/python3 - "src/GNUmakefile" <<'DISABLE_PROOT_PYTHON'
import pathlib
import sys

makefile = pathlib.Path(sys.argv[1])
lines = makefile.read_text(encoding="utf-8").splitlines()
replaced = {"HAS_SWIG": False, "HAS_PYTHON_CONFIG": False}
for index, line in enumerate(lines):
    for variable in replaced:
        if line.startswith(f"{variable} :=") and "$(shell" in line:
            lines[index] = f"{variable} :="
            replaced[variable] = True

if not all(replaced.values()):
    raise SystemExit(
        f"Could not disable automatic Python-extension detection in {makefile}"
    )
makefile.write_text("\n".join(lines) + "\n", encoding="utf-8")
DISABLE_PROOT_PYTHON
    fi
    case "$PKG" in
'''
if contents.count(needle) != 1:
    raise SystemExit(
        f"Could not identify the dependency compile insertion point in {script}"
    )
script.write_text(contents.replace(needle, replacement, 1), encoding="utf-8")
PY
        ./scripts/compile-dependencies
        local libexec_dir
        if [[ "$repair_proot" == true ]]; then
            libexec_dir="$target_libexec_dir"
        else
            libexec_dir="$(sed -n 's/^LIBEXECDIR := //p' builddir/Makefile)"
        fi
        if [[ -z "$libexec_dir" || "$libexec_dir" != /* ]]; then
            echo "ERROR: could not determine Apptainer's absolute LIBEXECDIR from builddir/Makefile." >&2
            exit 1
        fi
        run_privileged mkdir -p "$libexec_dir/apptainer/bin"
        run_privileged ./scripts/install-dependencies
        local bundled_proot="$libexec_dir/apptainer/bin/proot"
        if [[ ! -x "$bundled_proot" ]]; then
            echo "ERROR: Apptainer's dependency installer did not provide $bundled_proot." >&2
            exit 1
        fi
        local proot_libraries missing_libraries
        if ! proot_libraries="$(inspect_proot_libraries "$bundled_proot")"; then
            echo "ERROR: could not inspect the shared libraries required by $bundled_proot." >&2
            exit 1
        fi
        missing_libraries="$(grep 'not found' <<<"$proot_libraries" || true)"
        if [[ -n "$missing_libraries" ]]; then
            echo "ERROR: the bundled proot has unresolved shared libraries:" >&2
            printf '%s\n' "$missing_libraries" >&2
            exit 1
        fi
        if [[ "$repair_proot" == false ]]; then
            make -C builddir -j "$(nproc)"
            run_privileged make -C builddir install
        fi
    )
    rm -rf -- "$source_dir"
}

report_local_build_failure() {
    print_green_block <<'EOF'

ERROR: all local Apptainer build methods failed.
If the errors above mention user namespaces or "Operation not permitted",
this host/container blocks kernel operations needed to unpack and build the
image. PRoot cannot replace these kernel features, and sudo inside a
restricted container cannot enable them.

Run this update on a Linux host with unprivileged user namespaces enabled,
or ask the host administrator to enable user namespaces for this container
or provide a supported setuid Apptainer installation.
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
APPTAINER_LIBEXECDIR="$("$CONTAINER_RUNTIME" buildcfg LIBEXECDIR)"
if [[ -z "$APPTAINER_LIBEXECDIR" || "$APPTAINER_LIBEXECDIR" != /* ]]; then
    echo "ERROR: could not determine Apptainer's absolute LIBEXECDIR." >&2
    exit 1
fi
APPTAINER_PROOT="$APPTAINER_LIBEXECDIR/apptainer/bin/proot"
if [[ -x "$APPTAINER_PROOT" ]]; then
    if ! PROOT_LIBRARIES="$(inspect_proot_libraries "$APPTAINER_PROOT")"; then
        echo "ERROR: could not inspect Apptainer's bundled proot: $APPTAINER_PROOT" >&2
        exit 1
    fi
    if grep -q 'not found' <<<"$PROOT_LIBRARIES"; then
        echo "Bundled proot has missing runtime libraries; repairing it before continuing."
        install_apptainer --repair-proot "$APPTAINER_LIBEXECDIR"
        if ! PROOT_LIBRARIES="$(inspect_proot_libraries "$APPTAINER_PROOT")" \
            || grep -q 'not found' <<<"$PROOT_LIBRARIES"; then
            echo "ERROR: Apptainer's bundled proot still has unresolved runtime libraries after repair:" >&2
            printf '%s\n' "$PROOT_LIBRARIES" | grep 'not found' >&2 || true
            exit 1
        fi
    fi
fi

if [[ -n "$LOCAL_BASE" ]]; then
    LOCAL_BASE="$(realpath -- "$LOCAL_BASE")"
    if [[ ! -f "$LOCAL_BASE" ]]; then
        echo "ERROR: base SIF not found: $LOCAL_BASE" >&2
        exit 1
    fi
    if [[ "$(realpath -m -- "$BASE_IMAGE")" != "$LOCAL_BASE" ]]; then
        ln -sfn -- "$LOCAL_BASE" "$BASE_IMAGE"
    fi
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
        report_local_build_failure
        exit 1
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
                echo "Sudo build failed."
            fi
        else
            echo "sudo is unavailable."
        fi
        if [[ "$sudo_build_succeeded" == false ]]; then
            report_local_build_failure
            exit 1
        fi
    fi
fi
chmod +x "$BUILD_IMAGE"
mv -f -- "$BUILD_IMAGE" "$OUTPUT_DIR/geant-generator.sif"

echo "Built: $OUTPUT_DIR/geant-generator.sif"
echo "Config: $OUTPUT_DIR/config.json"
echo "Run: cd \"$OUTPUT_DIR\" && ./geant-generator.sif --dry-run"
