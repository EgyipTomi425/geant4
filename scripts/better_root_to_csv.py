import uproot
import numpy as np
import csv
import os
import sys
from tqdm import tqdm


def root_to_csv(input_file, step_size=1000):
    """
    Convert ROOT file data to CSV format.

    Args:
        input_file (str): Path to the input ROOT file
        step_size (int): Number of events to process at once

    Returns:
        str: Path to the created CSV file
    """
    # Generate output filename by replacing extension with .csv
    base_name = os.path.splitext(input_file)[0]
    output_file = f"{base_name}.csv"

    print(f"Reading ROOT file: {input_file}")
    print(f"Output CSV file: {output_file}")

    # Open the ROOT file
    with uproot.open(input_file) as file:
        events_tree = file["events"]

        # Define branches to read
        branches_to_read = [
            "LFHCALHits.cellID",
            "LFHCALHits.position.x",
            "LFHCALHits.position.y",
            "LFHCALHits.position.z",
            "LFHCALHits.energy",
            "LFHCALHits.contributions_begin",
            "LFHCALHits.contributions_end",
            "LFHCALHitsContributions.time",
            "MCParticles.momentum.x",
            "MCParticles.momentum.y",
            "MCParticles.momentum.z",
            "MCParticles.generatorStatus",
        ]

        total_entries = events_tree.num_entries
        print(f"Total events in file: {total_entries}")

        # Open CSV file for writing
        with open(output_file, 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)

            # Write header
            csv_writer.writerow([
                'event_number', 'cellID', 'x', 'y', 'z', 'energy',
                'earliest_contribution_time', 'incoming_momentum',
                'incoming_theta', 'incoming_phi'
            ])

            # Calculate total batches for progress bar
            total_batches = (total_entries + step_size - 1) // step_size

            # Process events in batches
            event_offset = 0
            for ak_array_chunk in tqdm(
                events_tree.iterate(
                    step_size=step_size,
                    library="ak",
                    expressions=branches_to_read,
                ),
                total=total_batches,
                desc="Processing events"
            ):
                # Loop over each event in the current batch
                for i in range(len(ak_array_chunk)):
                    event_number = event_offset + i

                    # Extract hit data for this event
                    cell_ids = ak_array_chunk["LFHCALHits.cellID"][i]
                    x_values = ak_array_chunk["LFHCALHits.position.x"][i]
                    y_values = ak_array_chunk["LFHCALHits.position.y"][i]
                    z_values = ak_array_chunk["LFHCALHits.position.z"][i]
                    energy_values = ak_array_chunk["LFHCALHits.energy"][i]

                    contrib_begin = ak_array_chunk["LFHCALHits.contributions_begin"][i]
                    contrib_end   = ak_array_chunk["LFHCALHits.contributions_end"][i]
                    contrib_times = ak_array_chunk["LFHCALHitsContributions.time"][i]

                    # Use generatorStatus==1 to identify the incoming generator-level particle.
                    mc_px = ak_array_chunk["MCParticles.momentum.x"][i]
                    mc_py = ak_array_chunk["MCParticles.momentum.y"][i]
                    mc_pz = ak_array_chunk["MCParticles.momentum.z"][i]
                    mc_status = ak_array_chunk["MCParticles.generatorStatus"][i]

                    incoming_momentum = np.nan
                    incoming_theta = np.nan
                    incoming_phi = np.nan
                    for px, py, pz, status in zip(mc_px, mc_py, mc_pz, mc_status):
                        if status == 1:
                            incoming_momentum = float(np.sqrt(px * px + py * py + pz * pz))
                            if incoming_momentum > 0:
                                # Polar angle (relative to the Z axis), in [0, pi]
                                incoming_theta = float(np.arccos(pz / incoming_momentum))
                                # Azimuthal angle (in the X-Y plane), in [-pi, pi]
                                incoming_phi = float(np.arctan2(py, px))
                            break

                    # Write each hit as a separate row
                    for cell_id, x, y, z, energy, begin, end in zip(
                        cell_ids, x_values, y_values, z_values, energy_values, contrib_begin, contrib_end
                    ):
                        hit_time = np.min(contrib_times[begin:end]) if end > begin else np.nan
                        csv_writer.writerow([
                            event_number, cell_id, x, y, z, energy, hit_time,
                            incoming_momentum, incoming_theta, incoming_phi
                        ])

                # Update event offset for next batch
                event_offset += len(ak_array_chunk)

    print(f"\nSuccessfully wrote data to: {output_file}")
    return output_file


def main():
    """Main function to handle command line arguments and execute conversion."""
    if len(sys.argv) < 2:
        print("Usage: python root_to_csv.py <input_root_file> [step_size]")
        print("\nExample:")
        print("  python root_to_csv.py output/ddsim/ddsim_vertex0_1-50GeV_e-_N100000_M50GeV.edm4hep.root")
        print("  python root_to_csv.py input.root 500")
        sys.exit(1)

    input_file = sys.argv[1]

    # Optional step size parameter
    step_size = 1000
    if len(sys.argv) >= 3:
        try:
            step_size = int(sys.argv[2])
        except ValueError:
            print(f"Warning: Invalid step_size '{sys.argv[2]}'. Using default value of {step_size}")

    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        sys.exit(1)

    # Perform conversion
    try:
        output_file = root_to_csv(input_file, step_size)
        print(f"\nConversion complete!")
    except Exception as e:
        print(f"Error during conversion: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
