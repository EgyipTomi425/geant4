#!/usr/bin/env python3
"""
check_secondaries.py

Sanity-check a CSV produced by root_to_csv_with_particles.py: reports what
fraction of hits come from the primary particle (particle_index == 0) vs.
secondaries (particle_index > 0), per event and overall, plus a breakdown
by secondary particle type.

Usage:
    python3 check_secondaries.py sim.csv
    python3 check_secondaries.py sim.csv --per-event
"""

import argparse
import csv
from collections import Counter, defaultdict


def analyze(path, per_event=False):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("No rows found.")
        return

    n_total = len(rows)
    n_primary = sum(1 for r in rows if r["particle_index"] == "0")
    n_secondary = sum(1 for r in rows if r["particle_index"] not in ("0", ""))
    n_empty = n_total - n_primary - n_secondary

    events = sorted(set(r["event_number"] for r in rows), key=int)
    secondary_type_counts = Counter(
        r["particle_type"] for r in rows if r["particle_index"] not in ("0", "")
    )

    print(f"File: {path}")
    print(f"Total hits: {n_total}")
    print(f"Events:     {len(events)}")
    print()
    print(f"Primary-particle hits   (particle_index == 0): {n_primary} "
          f"({100 * n_primary / n_total:.2f}%)")
    print(f"Secondary-particle hits (particle_index  > 0): {n_secondary} "
          f"({100 * n_secondary / n_total:.2f}%)")
    if n_empty:
        print(f"Hits with no contribution match: {n_empty} "
              f"({100 * n_empty / n_total:.2f}%)")
    print()

    if n_secondary == 0:
        print("*** No secondaries found at all. ***")
        print("If this happens even after keepAllParticles=True, check that")
        print("run_simulations.py's --better-root-to-csv actually points at")
        print("root_to_csv_with_particles.py (its default filename is")
        print("'better_root_to_csv.py'), and that the steering.py written")
        print("into the job's work_dir actually contains the new lines")
        print("(inspect one before it gets deleted, or rerun with --dry-run")
        print("removed and add a breakpoint / keep --work-dir around).")
    else:
        print("Secondary particle types:")
        for ptype, count in secondary_type_counts.most_common():
            print(f"  {ptype:12s} {count:6d} ({100 * count / n_secondary:.1f}% of secondaries)")

    if per_event:
        print()
        print("Per-event secondary fraction:")
        by_event = defaultdict(lambda: [0, 0])  # [primary, secondary]
        for r in rows:
            ev = r["event_number"]
            if r["particle_index"] == "0":
                by_event[ev][0] += 1
            elif r["particle_index"] not in ("", "0"):
                by_event[ev][1] += 1
        for ev in events:
            p, s = by_event[ev]
            total = p + s
            frac = 100 * s / total if total else 0.0
            print(f"  event {ev:>4s}: {total:5d} hits, {frac:6.2f}% secondary")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_file", help="CSV produced by root_to_csv_with_particles.py")
    p.add_argument("--per-event", action="store_true",
                    help="Also print a per-event secondary-fraction breakdown")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    analyze(args.csv_file, per_event=args.per_event)
