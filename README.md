# EIC Geant4 generator

[![Validate generator](https://github.com/EgyipTomi425/geant4/actions/workflows/validate.yml/badge.svg?branch=master)](https://github.com/EgyipTomi425/geant4/actions/workflows/validate.yml)

This repository builds a portable Apptainer image for running
Geant4 simulations through the EIC XL software environment. The generated SIF
contains the simulation runner and both ROOT-to-CSV converters. The JSON config
and generated data remain outside the image.

## Repository layout

- `update.sh` downloads the current EIC XL nightly base SIF and builds the
  generator image on top of it.
- `Apptainer.def` installs the runner and converters inside the image and
  selects the newest versioned EPIC detector setup available in that image at
  runtime.
- `scripts/config.json` is the default simulation configuration.
- `scripts/run_simulations.py` orchestrates simulations and conversion.
- `scripts/container_runscript.sh` selects the bundled detector and launches
  the simulation runner inside the image.
- `scripts/better_root_to_csv.py` is the basic hit-level converter.
- `scripts/root_to_csv_with_particles.py` exports contribution, particle,
  parent, and daughter data in the legacy 25-column format.
- `scripts/check_secondaries.py` summarizes secondary-particle rows in a CSV.
- `scripts/inspect_root_truth.py` inspects MC truth and contribution links in a
  ROOT file.
- `.github/workflows/validate.yml` runs the source/configuration checks on
  GitHub Actions.
- `tests/` checks config interpretation, planned jobs, and MC-truth steering.
- `apptainer/` is generated locally and ignored by Git. It contains the
  built SIF, a working copy of the config, and output folders.
- `.build/` is a local cache for the base EIC image and is ignored by Git.

## Requirements

- Linux on an architecture supported by the EIC XL image.
- Apptainer. If it is missing on Debian/Ubuntu, `update.sh` first tries the
  system package repositories. If the package is unavailable, it fetches the
  latest stable Apptainer release and Go toolchain, installs the build
  dependencies, and builds Apptainer from source. This requires network
  access and root or `sudo`. The apt package uses the version provided by the
  configured distribution repositories, which may not be the newest upstream
  release. The source build uses system Python only to read release metadata
  and explicitly disables PRoot's optional Python extension, independent of
  whether Conda or Python development tools are installed. It also checks that
  Apptainer's bundled helpers have their runtime libraries. If an existing
  installation has a bundled `proot` with unresolved libraries, the updater
  rebuilds and replaces that helper before continuing; it does not otherwise
  upgrade an existing Apptainer installation.
  For image builds, a non-root invocation tries fakeroot and then `sudo`;
  a root invocation tries a privileged build. If both fail because user
  namespaces are disabled, the updater rebuilds the SIF root filesystem
  directly with SquashFS and Apptainer's SIF tools, without creating a
  namespace. PRoot cannot replace those kernel features for Apptainer builds.
- Network access to the EIC container registry for a normal update. The EIC XL
  nightly image is pulled from `docker://eicweb/eic_xl:nightly`.
- Enough disk space for both the base and generator SIF images (each is several
  gigabytes).

No EIC files outside this repository are required for a clean clone. `update.sh`
creates `.build/` and `apptainer/` as needed, installs Apptainer if needed,
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
`apptainer/geant-generator.sif`, and copies the default JSON config to
`apptainer/config.json` the first time. It preserves an existing config on
later updates. To deliberately restore the repository default config:

```bash
./update.sh --reset-config
```

When upgrading an existing checkout that still has a generated `singularity/`
directory, the updater moves it to `apptainer/` on the first run if the new
directory does not already exist. This preserves the existing config, image,
and simulation outputs.

For an offline build or a local test using an existing compatible EIC image:

```bash
./update.sh --base-sif "$HOME/eic_xl-nightly.sif"
```

### Restricted hosts

Some containerized or cluster hosts disable the Linux user namespaces needed
for local image builds. When run as a normal user, the updater tries fakeroot
and then `sudo`; when run as root, it tries a privileged build. If these methods
fail because the host blocks user namespaces and Apptainer has no supported
setuid installation, the updater falls back to a namespace-free SIF rebuild.
It uses the SquashFS and SIF utilities bundled with Apptainer, preserves the
existing base image's metadata, and replaces its primary root filesystem with
the generator-enabled filesystem. If those utilities are missing, install
`squashfs-tools` or ask the host administrator to provide them. This fallback
also needs root or `sudo` to preserve filesystem ownership. `sudo` inside a
container cannot override restrictions imposed by the host kernel, and PRoot
is not a substitute for the kernel features required by Apptainer.

The downloaded base image is kept at `.build/eic_xl-nightly.sif` and is not
deleted after a successful build. On the next update, Apptainer pulls the
nightly image to this same path; its own cache may reuse downloaded image
layers. The base image is ignored by Git. The generated image and config are
kept in `apptainer/`; that directory is also not committed. The updater removes
its per-run build temporary directory on both success and failure, and removes
the `.build/tmp/` parent when it is empty. It stages the generator SIF
separately and only replaces the existing image after a successful build, so a
failed update does not leave a partial SIF or discard the previous working
image. To reclaim the base image's disk space manually, remove
`.build/eic_xl-nightly.sif`; the next update will download it again.

Build temporary files default to `.build/tmp/`, keeping large image extraction
and compression off potentially space-limited system temporary filesystems. Set
`APPTAINER_TMPDIR` before running `update.sh` to use another writable
location; the updater creates and removes its own subdirectory there, leaving
the user-selected directory and its other contents untouched.

## Run

Run from the generated `apptainer/` directory. The usual invocation names the
local config explicitly:

```bash
cd apptainer
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
inside the container. Apptainer normally binds the current working
directory, home, and `/tmp`. For a Slurm job or another external output path,
bind it explicitly:

```bash
OUTPUT_DIR="$PWD/slurm-output"
mkdir -p "$OUTPUT_DIR"
apptainer run --bind "$OUTPUT_DIR:$OUTPUT_DIR" \
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
- `no_field`: removes actual field elements from a private copy of the detector XMLs, including nested subsystem fields. Geometry constants and includes are preserved; the original installation is unchanged.
- `mc_truth`: stores contribution-linked MC particle ancestry. With this
  enabled, `auto` selects the particle-aware converter.
- `keep_all_particles`: additionally stores every Geant4 track; can greatly
  increase ROOT and CSV sizes. It is independent of `mc_truth` in the config,
  but enabling it implies MC truth, matching the original runner.
- `min_kinetic_energy` and `save_processes`: MC-truth particle-selection
  settings.
- `detailed_shower`: fills contribution PDG and detailed positions/lengths in ROOT; CSV exports contribution PDG but not the step-position/length columns.
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
runs), it also builds the EIC container, runs the both detector configurations from the default JSON
configuration with a CI event-count override (16 jobs, 100 events each), and verifies that all ROOT and CSV
outputs were created. If Apptainer is missing, `update.sh` installs it.

## Troubleshooting

- **No detector setup found:** rebuild from a valid EIC XL nightly image that
  includes a versioned EPIC detector installation.
- **Permission denied while building:** the updater tries its local build
  methods (fakeroot, `sudo`, and proot as a normal user; privileged build and
  proot as root). If all fail, the host/container kernel may be blocking
  required namespace operations; run the update on the host or ask the host
  administrator to enable Apptainer build support. Container `sudo` cannot
  override this restriction.
- **Config not found:** run from a directory containing `config.json` or pass
  `--config`.
- **Output or bind errors:** use a writable host path and bind it into the
  container if it is outside the runtime's default bind paths.
- **Conversion fails after simulation:** the ROOT file is retained in
  `roots_dir`; the failed job's scratch directory includes its steering file.

The default JSON uses 1,500,000 events, both `standalone` and `fulldet`, seeds 42
and 1024, no magnetic field, MC truth, detailed shower data, and `earliest`
contribution export. `--no-field` places a private detector copy beneath
`roots_dir/_nofield_compact/geometry`; this also keeps beamline constants
such as `offset_OMD_x` while removing fields. The CSV converter exports
`LFHCALHits` in both modes; full-detector ROOT files retain all hit collections.

The GitHub-hosted Ubuntu integration job enables user namespaces on its
ephemeral runner and checks them with `unshare` before the image build.
This addresses AppArmor restrictions that otherwise allow the image to build
but prevent Apptainer from starting it (`Operation not permitted`, exit 255).
It does not change settings on machines running this repository locally.

The particle-aware CSV export preserves the legacy 25-column schema, including
contribution counts, hit-local contribution indices, contribution energy/time/PDG,
and parent/daughter PDG lists. Step-position and step-length columns are not
exported; detailed shower data remain in ROOT and populate contribution PDG.
`energy` is the total hit energy even when only the earliest contribution is
exported. `contribution_index` is zero-based within the hit, not an index into
the event-wide contribution collection.

## Production run guide

The repository default is **1,500,000 events per particle/detector/seed
combination**, not 1,500,000 events across the whole run. Four particle types
(`e-`, `mu-`, `proton`, `pi+`), two detector modes (`standalone`, `fulldet`), and
two deterministic seeds (42 and 1024) create **16 jobs and 24,000,000 events**.
There is one injected primary particle per event. The configuration uses
0–20 GeV momentum, 7–36 degree polar angles, `FTFP_BERT`, no magnetic field,
MC truth, `keep_all_particles: false`, a 1 MeV truth-selection threshold,
`detailed_shower: true`, and `contributions: "earliest"`.

### Install and prepare the configuration

```bash
git clone https://github.com/EgyipTomi425/geant4.git
cd geant4
./update.sh
```

The updater installs Apptainer if necessary and builds the image. Network,
sudo/root access for installation, and sufficient disk space are required.
On an existing installation, `./update.sh` **preserves** `apptainer/config.json`.
Updating Git alone therefore does not change a previously copied event count.
Inspect your working config before starting. To deliberately replace it with
the repository default, discarding local configuration edits:

```bash
./update.sh --reset-config
```

Alternatively, copy the default to a separately named configuration and edit it:

```bash
cp scripts/config.json apptainer/production.json
```

Relative output paths are resolved from the selected JSON file's directory.
The default produces `apptainer/output/roots/` and `apptainer/output/csv/`.
Put the checkout or use explicit absolute output paths on the large data disk;
check its actual mount and free capacity with `df -hT`.

### Preview, then launch in tmux

From `geant4/apptainer`, preview the selected configuration without simulation:

```bash
cd apptainer
./geant-generator.sif --config ./config.json --dry-run
```

Check the 16 planned jobs and **N1500000** in their names before launching.
Use `--config ./production.json` instead if you made that separate config.
A small validation run can explicitly override the event count, for example
`--events 100 --jobs 2`; this creates real data, so use separate output paths.

For an intentionally authorized full run, in the same directory:

```bash
tmux new -s geant4
./geant-generator.sif --config ./config.json
```

Detach without stopping the run with **Ctrl+B**, then **D**. Reattach with
`tmux attach -t geant4`. By default, diagnostic output goes only to the
terminal: **file logging is disabled**. The runner does not create a
`generation.log` or `run.log` transcript. Do not add `tee` or shell output
redirection unless you explicitly want a persistent log. Tmux retains only
its configured scrollback buffer. ROOT/CSV outputs and temporary steering
files are simulation data, not terminal-log copies.
The configured `jobs: 100` is capped at the
16 actual jobs. To reduce simultaneous memory/CPU demand, add `--jobs 4`.
Do not run a second copy with the same output paths: names would collide.

### Understand the outputs

- `standalone` loads `epic_lfhcal_only.xml`; `fulldet` loads the complete
  `epic.xml`. In both cases the no-field geometry is a private copy with
  actual field definitions removed and geometry constants retained.
- Full-detector ROOT files retain the other detector hit collections as well.
  **CSV exports only LFHCALHits**, even for `fulldet`; other detector material
  still affects those hits because transport is simulated in the full geometry.
- CSV files are placed under `output/csv/regression-<particle>/`. The filename
  includes the detector, event count, momentum range, and seed index. `s1`
  means seed 42 and `s2` means seed 1024; those are indices, not seed values.
- The particle-aware CSV has exactly these 25 columns, in this order:

```csv
event_number,cellID,x,y,z,energy,earliest_contribution_time,incoming_momentum,incoming_theta,incoming_phi,n_contributions,contribution_index,contrib_pdg,contrib_type,contrib_energy,contrib_energy_fraction,contrib_time,particle_index,particle_pdg,particle_type,is_secondary,parent_pdg,parent_type,daughter_pdg,daughter_type
```

`x/y/z` are hit/cell positions in **mm**. Times are in **ns**, energies in
**GeV**, momentum in **GeV/c**, and CSV angles in **radians** (the JSON angle
limits are in degrees). `energy` is the total hit energy; `contrib_energy`
is the selected contribution's energy, and its fraction is relative to that
hit energy. There is no separate event-total-energy column. With `earliest`,
each hit appears once; summing `energy` by event gives deposited LFHCAL energy,
not total incoming energy or the sum over the whole detector.

`contribution_index` is local to the hit. `contrib_pdg/type` identify the actual
contribution; `particle_pdg/type` identify the linked saved MCParticle, which
can be an ancestor when truth thinning occurs. `parent_*` and `daughter_*`
are the saved immediate relations, with multiple entries separated by `;`.
They can describe interaction products as well as decays. Empty relations
are allowed, and the selection filters do not guarantee every secondary is
retained. Detailed shower fills contribution PDG and detailed ROOT step data;
step-position/length columns are intentionally absent from this legacy CSV.

A successful run ends with `Kesz: 16/16 sikeres.` and exit code 0. Verify 16
ROOT and 16 CSV files and inspect a CSV header. If a job fails, its scratch
folder under `roots_dir/_work/` is retained for diagnosis. The number of CSV
rows is the number of LFHCAL hits, not the number of injected events; events
with no LFHCAL hit have no CSV row.

### Storage estimate for N = 1,500,000

The following is a linear extrapolation from the validated 1,000-event test
with this 25-column schema, detailed shower enabled, earliest export, all four
particle types, and both seeds. TB below are decimal, not TiB.

| Detector mode | ROOT | CSV | Total |
|---|---:|---:|---:|
| standalone | 2.01 TB | 1.11 TB | 3.12 TB |
| fulldet | 5.01 TB | 0.68 TB | 5.70 TB |
| Both | **7.02 TB** | **1.79 TB** | **8.82 TB** |

Plan for **11–12 TB free**, including margin for statistical variation,
temporary files and container images. This is an estimate, not a measured
24-million-event run; a different detector/nightly version or configuration can
change the sizes. Keeping only CSV would reduce final retained size, but the
runner currently retains ROOT as well. A roughly 16 TiB disk has sufficient
capacity for this estimate if it has that free space available.

### CI versus production

CI validates the production JSON with `--dry-run`; it does not run 1.5 million
events per job. Its integration run explicitly overrides to **100 events** and
2 concurrent jobs, while checking both detector modes and all 16 output pairs.
Ubuntu user namespaces are enabled and checked before building Apptainer.
The nightly base SIF is cached per UTC day to avoid repeated OCI conversion;
the generator layer is rebuilt from the current scripts. New pushes cancel
obsolete runs on the same branch. Source/config changes are tested; a green
syntax/unit-test job alone does not mean the integration job has completed.
