#!/usr/bin/env python3
"""
ROOT -> CSV converter hitenkenti MC-truth informacioval.

Megtartja az eredeti hit-szintu oszlopokat, es hozzaadja, hogy az adott
hitet melyik reszecske csinalta, szekunder-e, honnan szarmazott (kozvetlen
szulo) es mive bomlott (kozvetlen leanyok). Teljes szarmazasi fat NEM
epitunk - csak egy szintet felfele es egy szintet lefele.

Uj oszlopok
-----------
n_contributions          hany Geant4 step jarult hozza ehhez a cellahoz
contribution_index       melyik contribution-t irja ez a sor (0-alapu)
contrib_pdg / _type      a contribution sajat PDG kodja (CaloHitContribution.PDG)
contrib_energy           a contribution energiaja [GeV]
contrib_energy_fraction  contrib_energy / hit energy
contrib_time             ennek a contributionnek az ideje [ns]
particle_index/_pdg/_type          a contributionert felelos MCParticle
is_secondary             1, ha a reszecske a szimulacioban keletkezett
                         (generatorStatus != 1), 0, ha a belott primer
parent_pdg / parent_type           mibol keletkezett (kozvetlen szulo)
daughter_pdg / daughter_type       mive bomlott (kozvetlen leanyok)

Tobb szulo/leany eseten a mezok pontosvesszovel elvalasztva; ha nincs,
akkor uresek.

FONTOS - miert lehet ures a truth informacio
---------------------------------------------
A DDG4 Geant4ParticleHandler alapertelmezesben megritkitja az MCParticles
rekordot, es a kidobott reszecskek hit-hozzajarulasait atkoti a tulelo
ososre. Ezen felul az alapertelmezett "Geant4TCUserParticleHandler" eldobja
azokat a reszecskeket, amelyek a tracking-terfogaton kivul keletkeztek --
vagyis az egesz kalorimeteres zaport. Ilyen ROOT fajlban minden hit a
primer reszecskehez tartozik, es ezen a konverter nem tud segiteni.

A szimulaciot igy kell inditani (lasd run_simulations.py --mc-truth):
    SIM.part.userParticleHandler = ""
    SIM.part.keepAllParticles = True        # vagy False + alacsony energiavagas
    SIM.part.saveProcesses = ['Decay', 'conv', ...]
    SIM.part.minimalKineticEnergy = 1*MeV

A contrib_pdg oszlop kivetel: az a CaloHitContribution sajat mezoje, ezt a
Geant4 a step-bol tolti ki, ezert akkor is helyes reszecsketipust ad, ha az
MCParticles rekord meg van ritkitva.

Podio elrendezesek
------------------
A podio verziok kozott valtozik, hogy a branch-ek hogyan neznek ki a
fajlban. Regebbi (lapos) forma:

    LFHCALHits.cellID
    _LFHCALHitsContributions_particle.index

Ujabb ("collection = egy branch") forma:

    LFHCALHits/LFHCALHits.cellID
    _LFHCALHitsContributions_particle/_LFHCALHitsContributions_particle.index

Ez a script mindkettot kezeli: felepit egy alias-terkepet a tenyleges
branch-nevekbol, es branch-enkent olvas (tree[teljes_kulcs].array(...)),
igy nem fugg az uproot expression-feloldasatol.

Hasznalat
---------
    python3 root_to_csv_with_particles.py input.root
    python3 root_to_csv_with_particles.py input.root --events 5
    python3 root_to_csv_with_particles.py input.root --contributions all
    python3 root_to_csv_with_particles.py input.root --list-branches
    python3 root_to_csv_with_particles.py input.root --hits EcalEndcapPHits \
        --output-dir ./out --output-file result.csv
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import uproot

try:
    from tqdm import tqdm
except ImportError:  # tqdm nem kotelezo
    def tqdm(iterable, **kwargs):
        return iterable


PDG_NAMES = {
    11: "e-", -11: "e+",
    13: "mu-", -13: "mu+",
    22: "gamma",
    111: "pi0", 211: "pi+", -211: "pi-",
    2212: "proton", -2212: "antiproton",
    2112: "neutron", -2112: "antineutron",
    12: "nu_e", -12: "anti_nu_e",
    14: "nu_mu", -14: "anti_nu_mu",
    16: "nu_tau", -16: "anti_nu_tau",
    321: "kaon+", -321: "kaon-",
    310: "K0S", 130: "K0L",
    311: "K0", -311: "anti_K0",
    15: "tau-", -15: "tau+",
    3122: "Lambda", -3122: "anti_Lambda",
    3222: "Sigma+", 3112: "Sigma-", 3212: "Sigma0",
    1000010020: "deuteron", 1000010030: "triton",
    1000020030: "He3", 1000020040: "alpha",
}


def particle_name(pdg):
    pdg = int(pdg)
    return PDG_NAMES.get(pdg, f"PDG_{pdg}")


# ---------------------------------------------------------------------------
# Branch-felismeres
# ---------------------------------------------------------------------------
def build_key_index(keys):
    """
    alias -> tenyleges (teljes) branch-kulcs terkep.

    A "LFHCALHits/LFHCALHits.cellID" kulcs aliasai peldaul:
        LFHCALHits/LFHCALHits.cellID   (maga a kulcs)
        LFHCALHits.cellID              (utolso '/' szegmens)
        LFHCALHits.LFHCALHits.cellID   ('/' -> '.')
    a "LFHCALHits/LFHCALHits/cellID" kulcse pedig:
        cellID, LFHCALHits.cellID, ...

    Igy a hivo mindig a rovid, olvashato nevet ("LFHCALHits.cellID")
    hasznalhatja, fuggetlenul a podio elrendezestol.
    """
    index = {}
    for key in keys:
        parts = key.split("/")
        leaf = parts[-1]
        aliases = {key, leaf, key.replace("/", ".")}
        if "." not in leaf and len(parts) >= 2:
            aliases.add(f"{parts[-2]}.{leaf}")
            aliases.add(f"{parts[0]}.{leaf}")
        for alias in aliases:
            index.setdefault(alias, key)
    return index


def pick_branch(index, candidates, must_contain=None):
    """
    Elso talalat a candidates listabol (alias vagy teljes kulcs); ha egyik
    sincs, fuzzy kereses: ".index"-re vegzodo alias, ami minden must_contain
    darabkat tartalmazza. None, ha nincs talalat.
    """
    for name in candidates:
        if name in index:
            return index[name]
    if must_contain:
        needles = [n.lower() for n in must_contain]
        for alias in sorted(index):
            low = alias.lower()
            if low.endswith(".index") and all(n in low for n in needles):
                return index[alias]
    return None


def resolve_branches(tree, hits_coll: str, contrib_coll: str, mc_coll: str):
    """logikai nev -> tenyleges branch-kulcs (vagy None)."""
    index = build_key_index(tree.keys())

    def direct(name):
        return index.get(name)

    hl, cl, ml = hits_coll.lower(), contrib_coll.lower(), mc_coll.lower()

    return index, {
        "cellID": direct(f"{hits_coll}.cellID"),
        "x": direct(f"{hits_coll}.position.x"),
        "y": direct(f"{hits_coll}.position.y"),
        "z": direct(f"{hits_coll}.position.z"),
        "energy": direct(f"{hits_coll}.energy"),
        "contrib_begin": direct(f"{hits_coll}.contributions_begin"),
        "contrib_end": direct(f"{hits_coll}.contributions_end"),
        # hit -> contribution relacio-index (ujabb podio). Ha nincs, a
        # begin/end kozvetlenul a contribution kollekciot szeleteli.
        "hit_contrib_rel": pick_branch(
            index,
            [f"_{hits_coll}_contributions.index", f"{hits_coll}.contributions.index"],
            must_contain=[f"_{hl}_contributions"],
        ),

        "contrib_time": direct(f"{contrib_coll}.time"),
        "contrib_energy": direct(f"{contrib_coll}.energy"),
        "contrib_pdg": direct(f"{contrib_coll}.PDG"),
        "contrib_step_x": direct(f"{contrib_coll}.stepPosition.x"),
        "contrib_step_y": direct(f"{contrib_coll}.stepPosition.y"),
        "contrib_step_z": direct(f"{contrib_coll}.stepPosition.z"),
        "contrib_step_length": direct(f"{contrib_coll}.stepLength"),
        "contrib_particle": pick_branch(
            index,
            [f"_{contrib_coll}_particle.index", f"{contrib_coll}.particle.index",
             f"{contrib_coll}#0.index"],
            must_contain=[f"_{cl}_particle"],
        ),

        "mc_pdg": direct(f"{mc_coll}.PDG"),
        "mc_status": direct(f"{mc_coll}.generatorStatus"),
        "mc_px": direct(f"{mc_coll}.momentum.x"),
        "mc_py": direct(f"{mc_coll}.momentum.y"),
        "mc_pz": direct(f"{mc_coll}.momentum.z"),

        "parents_begin": direct(f"{mc_coll}.parents_begin"),
        "parents_end": direct(f"{mc_coll}.parents_end"),
        "parents_index": pick_branch(
            index,
            [f"_{mc_coll}_parents.index", f"{mc_coll}.parents.index"],
            must_contain=[f"_{ml}_parents"],
        ),
        "daughters_begin": direct(f"{mc_coll}.daughters_begin"),
        "daughters_end": direct(f"{mc_coll}.daughters_end"),
        "daughters_index": pick_branch(
            index,
            [f"_{mc_coll}_daughters.index", f"{mc_coll}.daughters.index"],
            must_contain=[f"_{ml}_daughters"],
        ),
    }


def open_branch(tree, key):
    """
    Branch-objektum lekerese. A tree[...] indexeles verziofuggoen mashogy
    ertelmezi a '/' es '.' karaktereket, ezert tobb alakot is megprobalunk.
    """
    tried = []
    for candidate in (key, key.split("/")[-1], key.replace("/", ".")):
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            return tree[candidate]
        except Exception:
            continue
    raise KeyError(f"Nem sikerult megnyitni a branch-et: {key} (probaltuk: {tried})")


def relation_slice(begins, ends, indices, i, n_valid):
    """
    Egy reszecske relacio-indexei (edm4hep 'parents'/'daughters' minta).
    Csak azt az EGY reszecsket nezzuk, amelyikre szukseg van - nem epitunk
    terkepet az egesz esemenyre.
    """
    if begins is None or ends is None or indices is None:
        return []
    b, e = int(begins[i]), int(ends[i])
    return [int(x) for x in indices[b:e] if 0 <= int(x) < n_valid]


HEADER = [
    "event_number", "cellID", "x", "y", "z", "energy",
    "earliest_contribution_time",
    "contrib_step_x", "contrib_step_y", "contrib_step_z",
    "contrib_step_length",
    "incoming_momentum", "incoming_theta", "incoming_phi",
    "contribution_index", "particle_index", "particle_pdg", "particle_type",
    "is_secondary", "parent_type", "daughter_type",
]


def root_to_csv(input_file, output_dir=None, output_file=None,
                events=None, step_size=1000, hits_coll="LFHCALHits",
                mc_coll="MCParticles", contributions="earliest",
                quiet=False):
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_file}")

    contrib_coll = f"{hits_coll}Contributions"

    out_dir = Path(output_dir) if output_dir else input_path.parent
    out_name = output_file if output_file else input_path.with_suffix(".csv").name
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / out_name

    print(f"Reading ROOT file: {input_file}")
    print(f"Output CSV file: {output_path}")

    # Diagnosztika
    n_rows = 0
    n_hits = 0
    n_hits_with_particle = 0
    n_rows_secondary = 0
    distinct_particle_pdgs = {}
    distinct_contrib_pdgs = {}

    with uproot.open(input_file) as file:
        tree = file["events"]
        index, br = resolve_branches(tree, hits_coll, contrib_coll, mc_coll)

        missing_required = [k for k in ("cellID", "energy", "contrib_begin", "contrib_end")
                            if br[k] is None]
        if missing_required:
            sample = sorted(a for a in index if a.lower().startswith(hits_coll.lower()))[:25]
            raise RuntimeError(
                f"Hianyzo alapveto branch-ek a(z) '{hits_coll}' kollekciohoz: "
                f"{missing_required}. Ellenorizd a --hits kapcsolot. "
                f"Ilyen nevu branch-eket latok: {sample if sample else 'egyet sem'}. "
                f"A teljes listahoz: --list-branches"
            )

        if br["contrib_particle"] is None:
            print(
                "[FIGYELEM] Nem talalom a contribution -> MCParticle relaciot "
                f"(pl. _{contrib_coll}_particle.index). A particle_*/parent_*/"
                "daughter_* oszlopok uresek lesznek.",
                file=sys.stderr,
            )
        if br["contrib_pdg"] is None:
            print(f"[FIGYELEM] Nincs {contrib_coll}.PDG branch; a contrib_pdg "
                  "oszlop ures lesz.", file=sys.stderr)

        total_entries = tree.num_entries
        if events is not None:
            total_entries = min(total_entries, events)

        effective_step_size = step_size
        if events is not None:
            effective_step_size = max(1, min(step_size, events))

        print(f"Total events in file: {tree.num_entries}")
        if events is not None:
            print(f"Processing only the first {total_entries} event(s)")
        print(f"Batch (step) size: {effective_step_size}")
        print(f"Contribution mode: {contributions}")
        if br["hit_contrib_rel"] is not None:
            print(f"Hit -> contribution relacio: {br['hit_contrib_rel']}")
        else:
            print("Hit -> contribution relacio: nincs kulon index branch, "
                  "a begin/end kozvetlenul szeletel")

        # A branch-objektumokat egyszer oldjuk fel, nem batch-enkent.
        active = {k: open_branch(tree, v) for k, v in br.items() if v is not None}

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)

            n_batches = max(1, (total_entries + effective_step_size - 1) // effective_step_size)

            for batch in tqdm(range(n_batches), total=n_batches, desc="Processing events"):
                start = batch * effective_step_size
                stop = min(start + effective_step_size, total_entries)
                if start >= stop:
                    break

                # Branch-enkent olvasunk: a teljes kulccsal indexeles
                # determinisztikus, nem fugg az expression-feloldastol.
                arrays = {
                    logical: branch.array(
                        library="ak", entry_start=start, entry_stop=stop)
                    for logical, branch in active.items()
                }

                def get(logical, i, default=None):
                    if logical not in arrays:
                        return default
                    return np.asarray(arrays[logical][i])

                for i in range(stop - start):
                    # --- MC truth rekord ---
                    pdgs = get("mc_pdg", i, np.array([], dtype=np.int64))
                    n_mc = len(pdgs)
                    gen_status = get("mc_status", i, np.zeros(n_mc, dtype=np.int64))
                    mc_px = get("mc_px", i, np.zeros(n_mc))
                    mc_py = get("mc_py", i, np.zeros(n_mc))
                    mc_pz = get("mc_pz", i, np.zeros(n_mc))

                    parents_begin = get("parents_begin", i)
                    parents_end = get("parents_end", i)
                    parents_idx = get("parents_index", i)
                    daughters_begin = get("daughters_begin", i)
                    daughters_end = get("daughters_end", i)
                    daughters_idx = get("daughters_index", i)

                    # Belovo reszecske: elso MCParticle generatorStatus == 1.
                    incoming_momentum = np.nan
                    incoming_theta = np.nan
                    incoming_phi = np.nan
                    for k in range(n_mc):
                        if int(gen_status[k]) == 1:
                            p = float(np.sqrt(
                                mc_px[k] ** 2 + mc_py[k] ** 2 + mc_pz[k] ** 2))
                            incoming_momentum = p
                            if p > 0:
                                incoming_theta = float(np.arccos(mc_pz[k] / p))
                                incoming_phi = float(np.arctan2(mc_py[k], mc_px[k]))
                            break

                    # --- Hitek ---
                    cell_ids = get("cellID", i)
                    n_hit_in_event = len(cell_ids)
                    x_values = get("x", i, np.full(n_hit_in_event, np.nan))
                    y_values = get("y", i, np.full(n_hit_in_event, np.nan))
                    z_values = get("z", i, np.full(n_hit_in_event, np.nan))
                    energy_values = get("energy", i)

                    contrib_begin = get("contrib_begin", i)
                    contrib_end = get("contrib_end", i)
                    hit_contrib_rel = get("hit_contrib_rel", i)
                    contrib_times = get("contrib_time", i)
                    contrib_energies = get("contrib_energy", i)
                    contrib_pdgs = get("contrib_pdg", i)
                    contrib_step_x = get("contrib_step_x", i)
                    contrib_step_y = get("contrib_step_y", i)
                    contrib_step_z = get("contrib_step_z", i)
                    contrib_step_length = get("contrib_step_length", i)
                    contrib_particles = get("contrib_particle", i)

                    n_contrib_total = (
                        len(contrib_times) if contrib_times is not None
                        else (len(contrib_particles)
                              if contrib_particles is not None
                              else (len(contrib_pdgs)
                                    if contrib_pdgs is not None else 0)))

                    for hit_i in range(n_hit_in_event):
                        begin = max(0, int(contrib_begin[hit_i]))
                        end = max(0, int(contrib_end[hit_i]))
                        n_hits += 1
                        hit_energy = float(energy_values[hit_i])

                        # A begin/end vagy kozvetlenul a contribution
                        # kollekciot szeleteli (regi, lapos podio), vagy a
                        # relacio-index tombot (ujabb podio).
                        if hit_contrib_rel is not None:
                            positions = [int(hit_contrib_rel[j])
                                         for j in range(begin, end)]
                        else:
                            positions = list(range(begin, end))
                        positions = [p for p in positions if 0 <= p < n_contrib_total]
                        n_contrib = len(positions)

                        times = ([float(contrib_times[p]) for p in positions]
                                 if contrib_times is not None else [])
                        earliest_time = min(times) if times else np.nan

                        if n_contrib == 0:
                            selected = [None]
                        elif contributions == "all":
                            selected = list(range(n_contrib))
                        elif contributions == "dominant" and contrib_energies is not None:
                            selected = [int(np.argmax(
                                [float(contrib_energies[p]) for p in positions]))]
                        elif times:
                            selected = [int(np.argmin(times))]
                        else:
                            selected = [0]

                        hit_had_particle = False

                        for sel in selected:
                            c_pdg = c_type = c_energy = c_frac = c_time = ""
                            particle_index = particle_pdg = particle_type = ""
                            is_secondary = ""
                            parent_pdg = parent_type = ""
                            daughter_pdg = daughter_type = ""
                            step_x = step_y = step_z = step_length = ""

                            if sel is not None:
                                pos = positions[sel]

                                if contrib_step_x is not None:
                                    step_x = float(contrib_step_x[pos])
                                if contrib_step_y is not None:
                                    step_y = float(contrib_step_y[pos])
                                if contrib_step_z is not None:
                                    step_z = float(contrib_step_z[pos])
                                if contrib_step_length is not None:
                                    step_length = float(contrib_step_length[pos])
                                if contrib_pdgs is not None:
                                    c_pdg = int(contrib_pdgs[pos])
                                    c_type = particle_name(c_pdg)
                                    distinct_contrib_pdgs[c_pdg] = \
                                        distinct_contrib_pdgs.get(c_pdg, 0) + 1
                                if contrib_energies is not None:
                                    c_energy = float(contrib_energies[pos])
                                    if hit_energy > 0:
                                        c_frac = c_energy / hit_energy
                                if times:
                                    c_time = times[sel]

                                pidx = (int(contrib_particles[pos])
                                        if contrib_particles is not None else -1)

                                if 0 <= pidx < n_mc:
                                    hit_had_particle = True
                                    particle_index = pidx
                                    current_pdg = int(pdgs[pidx])
                                    particle_pdg = current_pdg
                                    particle_type = particle_name(current_pdg)
                                    distinct_particle_pdgs[current_pdg] = \
                                        distinct_particle_pdgs.get(current_pdg, 0) + 1

                                    is_secondary = 0 if int(gen_status[pidx]) == 1 else 1
                                    n_rows_secondary += is_secondary

                                    # Egy szint felfele: mibol keletkezett.
                                    parents = relation_slice(
                                        parents_begin, parents_end, parents_idx,
                                        pidx, n_mc)
                                    if parents:
                                        parent_pdgs = [int(pdgs[p]) for p in parents]
                                        parent_pdg = ";".join(str(p) for p in parent_pdgs)
                                        parent_type = ";".join(
                                            particle_name(p) for p in parent_pdgs)

                                    # Egy szint lefele: mive bomlott.
                                    daughters = relation_slice(
                                        daughters_begin, daughters_end, daughters_idx,
                                        pidx, n_mc)
                                    if daughters:
                                        daughter_pdgs = [int(pdgs[d]) for d in daughters]
                                        daughter_pdg = ";".join(
                                            str(d) for d in daughter_pdgs)
                                        daughter_type = ";".join(
                                            particle_name(d) for d in daughter_pdgs)

                            writer.writerow([
                                start + i,
                                int(cell_ids[hit_i]),
                                float(x_values[hit_i]),
                                float(y_values[hit_i]),
                                float(z_values[hit_i]),
                                hit_energy,
                                earliest_time,
                                step_x,
                                step_y,
                                step_z,
                                step_length,
                                incoming_momentum,
                                incoming_theta,
                                incoming_phi,
                                positions[sel] if sel is not None else "",
                                particle_index,
                                particle_pdg,
                                particle_type,
                                is_secondary,
                                parent_type,
                                daughter_type,
                            ])
                            n_rows += 1

                        if hit_had_particle:
                            n_hits_with_particle += 1

    print(f"\nSuccessfully wrote data to: {output_path}")

    if not quiet:
        print("\n=== Osszegzes ===")
        print(f"  hitek: {n_hits}, kiirt sorok: {n_rows}")
        print(f"  hitek ervenyes MCParticle linkkel: {n_hits_with_particle}")
        frac_sec = 100.0 * n_rows_secondary / n_rows if n_rows else 0.0
        if n_rows:
            print(f"  szekunder reszecskehez tartozo sorok: "
                  f"{n_rows_secondary} ({frac_sec:.1f}%)")
        print("\n  Reszecsketipusok (MCParticle link szerint), top 10:")
        for pdg, cnt in sorted(distinct_particle_pdgs.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    {particle_name(pdg):>12s} (PDG {pdg:>8d}): {cnt}")
        if distinct_contrib_pdgs and set(distinct_contrib_pdgs) == {0}:
            print(
                "\n  [INFO] A contrib_pdg oszlop vegig 0. A Geant4Output2EDM4hep\n"
                "  csak DETAILED_MODE-ban tolti ki a CaloHitContribution.PDG /\n"
                "  stepPosition / stepLength mezoket. Ha kell a step-szintu PDG:\n"
                "      SIM.enableDetailedShowerMode = True\n"
                "  vagy: run_simulations.py ... --detailed-shower\n"
                "  A szulo/leany oszlopokhoz NEM kell - azok a contribution ->\n"
                "  MCParticle relaciobol jonnek, es mukodnek."
            )
        elif distinct_contrib_pdgs:
            print("\n  Reszecsketipusok (CaloHitContribution.PDG szerint), top 10:")
            for pdg, cnt in sorted(distinct_contrib_pdgs.items(), key=lambda kv: -kv[1])[:10]:
                print(f"    {particle_name(pdg):>12s} (PDG {pdg:>8d}): {cnt}")
        if n_rows and frac_sec < 1.0:
            print(
                "\n  [FIGYELEM] Gyakorlatilag minden hit a belott primerhez\n"
                "  tartozik, tehat a ROOT fajlban nincsenek szekunderek.\n"
                "  Ez a DDG4 MC-truth ritkitasa, nem a konverter hibaja.\n"
                "  Ujra kell szimulalni ezekkel a steering beallitasokkal:\n"
                "      SIM.part.userParticleHandler = \"\"\n"
                "      SIM.part.keepAllParticles = True\n"
                "  vagy: run_simulations.py ... --mc-truth --keep-all-particles",
                file=sys.stderr,
            )

    return str(output_path)


def list_branches(input_file, hits_coll, mc_coll):
    contrib_coll = f"{hits_coll}Contributions"
    with uproot.open(input_file) as file:
        tree = file["events"]
        keys = list(tree.keys())
        print(f"{len(keys)} branch az 'events' faban:\n")
        for k in sorted(keys):
            print(f"  {k}")
        _, br = resolve_branches(tree, hits_coll, contrib_coll, mc_coll)
        print("\nFeloldott branch-ek:")
        for logical, key in br.items():
            print(f"  {logical:>18s} : {key if key else '-- NINCS MEG --'}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a ROOT file with per-hit MC particle information "
                    "(secondary flag, parent, daughters) into a CSV file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_file", help="Path to the input ROOT file")
    parser.add_argument("--output-dir", default=None,
                        help="Directory for the output CSV (default: input file's directory)")
    parser.add_argument("--output-file", default=None,
                        help="Output CSV filename (default: <input_file_stem>.csv)")
    parser.add_argument("--events", type=int, default=None,
                        help="Only process the first N events (default: all)")
    parser.add_argument("--step-size", type=int, default=1000,
                        help="Events read per batch; capped to --events when smaller")
    parser.add_argument("--hits", dest="hits_coll", default="LFHCALHits",
                        help="Name of the SimCalorimeterHit collection")
    parser.add_argument("--mc-collection", dest="mc_coll", default="MCParticles",
                        help="Name of the MCParticle collection")
    parser.add_argument("--contributions", choices=["earliest", "dominant", "all"],
                        default="earliest",
                        help="earliest: egy sor hitenkent a legkorabbi contribution "
                             "alapjan; dominant: a legnagyobb energiaju contribution; "
                             "all: minden contribution kulon sorba")
    parser.add_argument("--list-branches", action="store_true",
                        help="Csak kilistazza a fa branch-eit es a feloldast, majd kilep")
    parser.add_argument("--quiet", action="store_true",
                        help="Ne irja ki a vegso diagnosztikai osszegzest")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_branches:
        list_branches(args.input_file, args.hits_coll, args.mc_coll)
        return

    if args.events is not None and args.events <= 0:
        print("Error: --events must be a positive integer.", file=sys.stderr)
        sys.exit(1)
    if args.step_size <= 0:
        print("Error: --step-size must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    try:
        root_to_csv(
            args.input_file,
            output_dir=args.output_dir,
            output_file=args.output_file,
            events=args.events,
            step_size=args.step_size,
            hits_coll=args.hits_coll,
            mc_coll=args.mc_coll,
            contributions=args.contributions,
            quiet=args.quiet,
        )
    except Exception as exc:
        print(f"Error during conversion: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()