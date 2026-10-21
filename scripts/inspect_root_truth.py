#!/usr/bin/env python3
"""
inspect_root_truth.py

Bypasses root_to_csv_with_particles.py entirely and looks directly at the
MC truth in a sim.root file, to tell apart two possible failure modes:

  (A) ddsim itself only ever records the primary particle in MCParticles
      for each event (i.e. keepAllParticles=True did not actually take
      effect, or every secondary is being pruned back onto the primary
      before it ever reaches the truth record).

  (B) MCParticles genuinely contains many secondaries, but the hit
      contributions (_LFHCALHitsContributions_particle.index) all still
      point at index 0 -- which would point at a bug/quirk in how
      contributions are filled or interpreted, not at the steering file.

Usage:
    python3 inspect_root_truth.py sim.root
    python3 inspect_root_truth.py sim.root --events 5
"""

import argparse
import numpy as np
import uproot


def inspect(path, max_events=None):
    with uproot.open(path) as f:
        tree = f["events"]
        n_events = tree.num_entries
        if max_events:
            n_events = min(n_events, max_events)

        branches = [
            "MCParticles.PDG",
            "MCParticles.generatorStatus",
            "_LFHCALHitsContributions_particle.index",
            "LFHCALHits.contributions_begin",
            "LFHCALHits.contributions_end",
        ]

        print(f"File: {path}")
        print(f"Total events in file: {tree.num_entries}\n")

        chunk = tree.arrays(branches, entry_start=0, entry_stop=n_events, library="ak")

        for i in range(n_events):
            pdgs = np.asarray(chunk["MCParticles.PDG"][i])
            statuses = np.asarray(chunk["MCParticles.generatorStatus"][i])
            contrib_particles = np.asarray(chunk["_LFHCALHitsContributions_particle.index"][i])

            n_mc = len(pdgs)
            unique_contrib_idx = np.unique(contrib_particles) if len(contrib_particles) else np.array([])

            print(f"--- event {i} ---")
            print(f"  MCParticles in truth record: {n_mc}")
            if n_mc:
                pdg_counts = {}
                for p in pdgs:
                    pdg_counts[int(p)] = pdg_counts.get(int(p), 0) + 1
                print(f"  PDG codes present: {pdg_counts}")
            print(f"  # hit contributions this event: {len(contrib_particles)}")
            print(f"  distinct particle indices referenced by contributions: "
                  f"{unique_contrib_idx.tolist()}")
            print()

    print("Diagnosis:")
    print("- If 'MCParticles in truth record' is ~1-3 for most events even at")
    print("  high momentum -> ddsim/steering is the problem (keepAllParticles")
    print("  isn't producing secondaries in the truth record at all).")
    print("- If MCParticles is large (tens/hundreds) but 'distinct particle")
    print("  indices referenced by contributions' is always just [0] -> the")
    print("  hit-contribution recording or root_to_csv_with_particles.py's")
    print("  indexing is the problem, not the steering file.")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root_file")
    p.add_argument("--events", type=int, default=5,
                    help="Number of events to inspect (default: 5)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    inspect(args.root_file, max_events=args.events)
