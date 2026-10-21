# EIC Geant4 generator

This repository builds a portable Singularity/Apptainer image for running
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
- `singularity/` is generated locally and ignored by Git. It contains the
  built SIF, a working copy of the config, and output folders.
- `.build/` is a local cache for the base EIC image and is ignored by Git.

## Requirements

- Linux on an architecture supported by the EIC XL image.
- Apptainer or Singularity. `update.sh` first tries an unprivileged fakeroot
  build. If fakeroot is unavailable or not configured, it falls back to `sudo`.
  A root shell can build without either mechanism.
- Network access to the EIC container registry for a normal update. The EIC XL
  nightly image is pulled from `docker://eicweb/eic_xl:nightly`.
- Enough disk space for both the base and generator SIF images (each is several
  gigabytes).

No files outside this repository are required for a clean clone. `update.sh`
creates `.build/` and `singularity/` as needed, and fetches the EIC base image
itself. `--base-sif` is only an optional override for an existing local base
image; it is not needed for the normal fresh-clone workflow.

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
./update.sh --base-sif /path/to/eic_xl-nightly.sif
```

The downloaded base image is kept in `.build/eic_xl-nightly.sif`; it is not
committed. The generated image and config are kept in `singularity/`; that
directory is also not committed.

## Run

The default config is discovered next to the SIF:

```bash
cd singularity
./geant-generator.sif
```

Preview the jobs without running simulations:

```bash
./geant-generator.sif --dry-run
```

Use another config:

```bash
./geant-generator.sif --config /path/to/config.json
```

The config must be readable and its output directory must be writable from
inside the container. Singularity/Apptainer normally binds the current working
directory, home, and `/tmp`. Bind other host directories explicitly, for
example:

```bash
apptainer run --bind /data/simulations:/data/simulations \
  /path/to/geant-generator.sif \
  --config /data/simulations/config.json
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

## Troubleshooting

- **No detector setup found:** rebuild from a valid EIC XL nightly image that
  includes a versioned EPIC detector installation.
- **Permission denied while building:** configure Apptainer/Singularity
  fakeroot support, or use an account authorized to build images with `sudo`.
- **Config not found:** place `config.json` beside the SIF or pass `--config`.
- **Output or bind errors:** use a writable host path and bind it into the
  container if it is outside the runtime's default bind paths.
- **Conversion fails after simulation:** the ROOT file is retained in
  `roots_dir`; the failed job's scratch directory includes its steering file.
