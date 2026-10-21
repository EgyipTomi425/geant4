# EIC Geant4 generator

[![Validate generator](https://github.com/EgyipTomi425/geant4/actions/workflows/validate.yml/badge.svg?branch=master)](https://github.com/EgyipTomi425/geant4/actions/workflows/validate.yml)

This repository builds a portable SingularityCE image for running
Geant4 simulations through the EIC XL software environment. The generated SIF
contains the simulation runner and both ROOT-to-CSV converters. The JSON config
and generated data remain outside the image.

## Repository layout

- `update.sh` downloads the current EIC XL nightly base SIF and builds the
  generator image on top of it.
- `Singularity.def` installs the runner and converters inside the image and
  selects the newest versioned EPIC detector setup available in that image at
  runtime.
- `scripts/config.json` is the default simulation configuration.
- `scripts/run_simulations.py` orchestrates simulations and conversion.
- `scripts/better_root_to_csv.py` is the basic hit-level converter.
- `scripts/root_to_csv_with_particles.py` exports contribution, particle,
  parent, daughter, and optional detailed-step data.
- `scripts/check_secondaries.py` summarizes secondary-particle rows in a CSV.
- `scripts/inspect_root_truth.py` inspects MC truth and contribution links in a
  ROOT file.
- `.github/workflows/validate.yml` runs the source/configuration checks on
  GitHub Actions.
- `tests/` checks config interpretation, planned jobs, and MC-truth steering.
- `singularity/` is generated locally and ignored by Git. It contains the
  built SIF, a working copy of the config, and output folders.
- `.build/` is a local cache for the base EIC image and is ignored by Git.

## Requirements

- Linux on an architecture supported by the EIC XL image.
- SingularityCE. If it is missing on Debian/Ubuntu, `update.sh` installs the
  build dependencies and compiles the pinned SingularityCE release. This
  requires network access and root or `sudo`. The updater first tries an
  unprivileged fakeroot build, then a `sudo` build, and finally an unprivileged
  `proot` build. If all local methods fail, it tries the SingularityCE remote
  build service. Remote builds require a Singularity Container Services
  account and a prior `singularity remote login`. On Debian/Ubuntu the updater
  installs `proot` if needed; installing packages requires root or `sudo`.
  The remote fallback uses the EIC XL nightly OCI image directly and uploads
  the repository's generator scripts as build context; it cannot honor a
  custom local `--base-sif` image.
- Network access to the EIC container registry for a normal update. The EIC XL
  nightly image is pulled from `docker://eicweb/eic_xl:nightly`.
- Enough disk space for both the base and generator SIF images (each is several
  gigabytes).

No EIC files outside this repository are required for a clean clone. `update.sh`
creates `.build/` and `singularity/` as needed, installs SingularityCE if needed,
and fetches the EIC base image itself. Python and the Geant/EIC Python
environment are provided by the base image. `--base-sif` is only an optional
override for an existing local base image; it is not needed for a normal
fresh-clone build.

## Build or update

From the repository root:

```bash
./update.sh
```

The script pulls the latest `eicweb/eic_xl:nightly` image, builds
`singularity/geant-generator.sif`, and copies the default JSON config to
`singularity/config.json` the first time. It preserves an existing config on
later updates. To deliberately restore the repository default config:

```bash
./update.sh --reset-config
```

For an offline build or a local test using an existing compatible EIC image:

```bash
./update.sh --base-sif "$HOME/eic_xl-nightly.sif"
```

### Restricted hosts

Some containerized or cluster hosts disable the Linux user namespaces needed
for local image builds. The updater automatically tries fakeroot, `sudo`, and
`proot`; if those fail, it attempts a SingularityCE remote build. Remote
building requires a Singularity Container Services account with remote-build
access. Log in once as the same user who will run the updater:

```bash
singularity remote login
./update.sh
```

The login command may ask for account credentials or a token in the terminal;
do not put credentials in the repository or share them. The updater prints
the login command only when the remote service reports an authentication
error. If authentication succeeds but the remote builder fails or returns no
image, it says this is a build-service failure and directs you to that job's
logs instead of asking you to log in again. These instructions are highlighted
in green in an interactive terminal; color is disabled when output is
redirected or `NO_COLOR` is set. Without remote-build access, ask the host
administrator to enable the required local build support or build on another
machine.

The downloaded base image is kept in `.build/eic_xl-nightly.sif`; it is a
persistent cache and is not deleted after a successful build, so later updates
can reuse it. It is ignored by Git. The generated image and config are kept in
`singularity/`; that directory is also not committed. The updater removes its
per-run build temporary directory on both success and failure, and removes the
`.build/tmp/` parent when it is empty. It stages the generator SIF separately
and only replaces the existing image after a successful build, so a failed
update does not leave a partial SIF or discard the previous working image. To
reclaim the base image's disk space manually, remove `.build/eic_xl-nightly.sif`;
the next update will download it again.

Build temporary files default to `.build/tmp/`, keeping large image extraction
and compression off potentially space-limited system temporary filesystems. Set
`SINGULARITY_TMPDIR` before running `update.sh` to use another writable
location; the updater creates and removes its own subdirectory there, leaving
the user-selected directory and its other contents untouched. If a local build
fails, the updater tries the SingularityCE remote build service after the local
fallbacks. Remote builds use the EIC XL nightly OCI image and upload the
generator scripts as build context. A custom local `--base-sif` cannot be used
by this fallback. Sign in once before running the updater if the remote
fallback may be needed:

```bash
singularity remote login
```

## Run

Run from the generated `singularity/` directory. The usual invocation names the
local config explicitly:

```bash
cd singularity
./geant-generator.sif --config ./config.json
```

Without `--config`, the container also looks for `./config.json` in the current
working directory. Preview the jobs without running simulations:

```bash
./geant-generator.sif --dry-run
```

Use another config from the current directory:

```bash
cp config.json custom-config.json
./geant-generator.sif --config ./custom-config.json
```

The config must be readable and its output directory must be writable from
inside the container. Singularity normally binds the current working
directory, home, and `/tmp`. For a Slurm job or another external output path,
bind it explicitly:

```bash
OUTPUT_DIR="$PWD/slurm-output"
mkdir -p "$OUTPUT_DIR"
singularity run --bind "$OUTPUT_DIR:$OUTPUT_DIR" \
  ./geant-generator.sif --config ./config.json \
  --output-dir "$OUTPUT_DIR/csv" --roots-dir "$OUTPUT_DIR/roots"
```

Relative `output_dir`, `roots_dir`, and `work_dir` paths are resolved relative
to the config file. They are created as needed. With `no_field: true`, the
runner also creates `roots_dir/_nofield_compact/` containing a field-stripped
copy of the detector compact XML. Successful per-job scratch directories are
removed; failed ones are retained for diagnosis. ROOT files remain in
`roots_dir`, CSV files are grouped below `output_dir/regression-<particle>/`.

## Configuration

The JSON keys correspond to runner option names (for example `no_field` is
`--no-field`). Required simulation values include `particles`, `events`,
`theta_min`, `theta_max`, `momentum_min`, and `momentum_max`.

Key options:

- `num_seeds`: number of seeds per particle/detector/event combination.
  With `random_seeds: false`, the first seeds are fixed at 42 and 1024.
- `no_field`: strips field includes from the compact geometry.
- `mc_truth`: stores contribution-linked MC particle ancestry. With this
  enabled, `auto` selects the particle-aware converter.
- `keep_all_particles`: additionally stores every Geant4 track; can greatly
  increase ROOT and CSV sizes. It is independent of `mc_truth` in the config,
  but enabling it implies MC truth, matching the original runner.
- `min_kinetic_energy` and `save_processes`: MC-truth particle-selection
  settings.
- `detailed_shower`: enables contribution step PDG, positions, and lengths.
- `contributions`: particle-aware converter mode: `earliest`, `dominant`, or
  `all`.
- `jobs`: maximum number of parallel workers; the runner caps this to the
  actual number of planned jobs.

JSON does not support comments. The runner ignores `_comment...` keys, which
are used in the supplied config for explanations.

## Detector version selection

At startup, the container finds versioned installations matching
`/opt/detector/epic-<version>/bin/thisepic.sh` and chooses the greatest
version using version-aware sorting. It then sets `DETECTOR_CACHE` to that
installation's `DETECTOR_PATH`. It does not depend on a hard-coded EPIC
version such as `epic-26.07.1`. If no versioned detector setup exists, it
stops with an explicit error.

The selected detector version and the base EIC nightly image can change when
the image is rebuilt; use the dry-run and a small simulation to validate a new
build before a large production run.

## Continuous integration

GitHub Actions runs on Ubuntu 26.04. It performs Python/JSON/shell syntax
checks, unit tests for config and job planning, and the default-config dry-run
on pushes and pull requests. On pushes to `master` (and manual `workflow_dispatch`
runs), it also builds the EIC container, runs the full default JSON
configuration (8 jobs, 100 events each), and verifies that all ROOT and CSV
outputs were created. If SingularityCE is missing, `update.sh` installs it.

## Troubleshooting

- **No detector setup found:** rebuild from a valid EIC XL nightly image that
  includes a versioned EPIC detector installation.
- **Permission denied while building:** the updater tries fakeroot, `sudo`,
  and an unprivileged `proot` build, then tries the SingularityCE remote build
  service. If the service reports a missing/invalid token, the updater prints
  the `singularity remote login` and retry commands. Other remote failures,
  including a build that returns no image, are identified separately; inspect
  that job's logs in the Singularity Container Services account rather than
  repeating login. If remote builds are unavailable, configure user namespaces
  on the host or build outside the restricted container.
- **Config not found:** run from a directory containing `config.json` or pass
  `--config`.
- **Output or bind errors:** use a writable host path and bind it into the
  container if it is outside the runtime's default bind paths.
- **Conversion fails after simulation:** the ROOT file is retained in
  `roots_dir`; the failed job's scratch directory includes its steering file.
