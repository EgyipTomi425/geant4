#!/usr/bin/env python3
"""
run_simulations.py

CLI a ddsim szimulaciok es a ROOT->CSV konverzio parhuzamos futtatasahoz,
regresszios adathalmazok generalasahoz.

Pelda:
    python3 run_simulations.py \\
        --particles e- mu- proton \\
        --num-seeds 4 \\
        --events 100000 300000 \\
        --theta-min 7 --theta-max 36 \\
        --momentum-min 0 --momentum-max 20 \\
        --detector standalone fulldet \\
        --jobs 6

Mezo nelkuli futtatas (a compact XML-bol kiszurve a "compact/fields/..."
include-ok, pl. a szolenoid es a nyalabvezetek-magnesek):
    python3 run_simulations.py \\
        --particles e- \\
        --num-seeds 1 \\
        --events 10000 \\
        --theta-min 7 --theta-max 36 \\
        --momentum-min 0 --momentum-max 20 \\
        --detector standalone \\
        --no-field

MC-truth (hitenkenti szulo/leany informacio) futtatas:
    python3 run_simulations.py \\
        --particles proton \\
        --num-seeds 1 \\
        --events 1000 \\
        --theta-min 3 --theta-max 36 \\
        --momentum-min 0 --momentum-max 20 \\
        --detector standalone \\
        --no-field \\
        --mc-truth

Kornyezeti valtozok:
    DETECTOR_PATH   - mindig szukseges (a compact fajlok gyokere)
    DETECTOR_CONFIG - csak --detector fulldet eseten szukseges
"""

import argparse
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:  # tqdm nem kotelezo, csak szebb a kimenet
    def tqdm(iterable, **kwargs):
        return iterable


# ---------------------------------------------------------------------------
# Reszecske -> (gun nev, fajlnevben hasznalt nev, regresszios csalad/mappa)
# ---------------------------------------------------------------------------
PARTICLES = {
    "e-":     {"gun_name": "e-",     "file_name": "electron",  "family": "electron"},
    "mu-":    {"gun_name": "mu-",    "file_name": "muon",      "family": "muon"},
    "proton": {"gun_name": "proton", "file_name": "proton",    "family": "proton"},
    "pi+":    {"gun_name": "pi+",    "file_name": "pion",      "family": "pion"},
    "pi0":    {"gun_name": "pi0",    "file_name": "pion0",     "family": "pion0"},
    "kaon+":  {"gun_name": "kaon+",  "file_name": "kaonplus",  "family": "kaon"},
}

STEERING_TEMPLATE = """from DDSim.DD4hepSimulation import DD4hepSimulation
from g4units import mm, GeV, MeV

SIM = DD4hepSimulation()
SIM.gun.thetaMin = "{theta_min}*deg"
SIM.gun.thetaMax = "{theta_max}*deg"
SIM.gun.distribution = "cos(theta)"
SIM.gun.momentumMin = "{momentum_min}*GeV"
SIM.gun.momentumMax = "{momentum_max}*GeV"
SIM.numberOfEvents = "{num_events}"
SIM.gun.particle = "{particle}"
SIM.outputFile = "sim.root"
SIM.random.seed = {seed}
SIM.physicsList = "{physics_list}"
{steering_extras}"""


# ---------------------------------------------------------------------------
# --mc-truth tamogatas
#
# MIERT KELL EZ:
# A DDG4 Geant4ParticleHandler alapertelmezesben MEGRITKITJA (prune) az
# MCParticles rekordot. Amit kidob, annak a hit-hozzajarulasait
# atkoti a legkozelebbi tulelo ososre (recombineParents() ->
# m_equivalentTracks[g4_id] = p->g4Parent). Ezert lesz a
# _LFHCALHitsContributions_particle.index gyakorlatilag minden hitnel 0,
# vagyis a belovo primer reszecske: a masodlagosok nincsenek benne a
# fajlban, nem a konverter hibazik.
#
# Ket dolog vagja ki oket:
#   1) SIM.part.userParticleHandler (alap: "Geant4TCUserParticleHandler")
#      Ez a kezelo eldobja azokat a reszecskeket, amelyek a tracking-
#      terfogaton KIVUL keletkeztek -> vagyis pont az egesz kaloriméteres
#      zaport. Ures stringgel kikapcsolhato.
#   2) SIM.part.keepAllParticles (alap: False) + minimalKineticEnergy
#      (alap: 1 MeV) + saveProcesses (alap: ['Decay']).
#
# Ket szint:
#   --mc-truth              -> userParticleHandler kikapcsolva, tagabb
#                              saveProcesses lista, allithato energiavagas.
#                              Megmarad minden reszecske, ami hitet csinalt,
#                              es a szulo-lancuk. Ez kell a hitenkenti
#                              "honnan jott / mibe bomlott" oszlopokhoz.
#   --keep-all-particles    -> ezen felul keepAllParticles = True, azaz
#                              MINDEN Geant4 track bekerul. Teljes igazsag,
#                              de a fajlmeret nagysagrendekkel no.
# ---------------------------------------------------------------------------
MC_TRUTH_TEMPLATE = """
# --- MC truth (hitenkenti reszecske-szarmazas) ---
SIM.part.userParticleHandler = ""
SIM.part.keepAllParticles = {keep_all}
SIM.part.saveProcesses = {save_processes}
SIM.part.minimalKineticEnergy = {min_kinetic_energy}*MeV
"""


# ---------------------------------------------------------------------------
# --detailed-shower
#
# A CaloHitContribution.PDG mezot (es a stepPosition/stepLength mezoket) a
# Geant4Output2EDM4hep CSAK akkor tolti ki, ha a szenzitiv detektor
# HitCreationMode = 2 (DETAILED_MODE):
#
#     if ( hit_creation_mode == Geant4Sensitive::DETAILED_MODE ) {
#         sCaloHitCont.setPDG( c.pdgID );
#         ...
#     }
#
# Enelkul a contrib_pdg oszlop mindenhol 0. A ddsim kapcsoloja ehhez a
# SIM.enableDetailedShowerMode (alap: False), ami HitCreationMode = 2-t
# allit minden szenzitiv akcion.
#
# Ez nem szukseges a szulo/leany informaciohoz - azt a contribution ->
# MCParticle relacio adja, ami --mc-truth mellett mar mukodik. Csak akkor
# kapcsold be, ha tenyleg kell a step-szintu PDG / pozicio; cserebe nonek a
# contribution rekordok.
# ---------------------------------------------------------------------------
DETAILED_SHOWER_LINE = 'SIM.enableDetailedShowerMode = True\n'


def build_steering_extras(args: argparse.Namespace) -> str:
    """A steering fajl vegere kerulo extra beallitasok."""
    block = ""
    if args.mc_truth:
        block += MC_TRUTH_TEMPLATE.format(
            keep_all=bool(args.keep_all_particles),
            save_processes=repr(list(args.save_processes)),
            min_kinetic_energy=float(args.min_kinetic_energy),
        )
    if args.detailed_shower:
        block += "\n# --- step-szintu contribution reszletek ---\n"
        block += DETAILED_SHOWER_LINE
    return block


# ---------------------------------------------------------------------------
# --no-field tamogatas
#
# A magneses (es nyalabvezetek-) mezot a compact XML "compact/fields/..."
# alatti fajlokra mutato <include ref="..."/> sorai definialjak. A ddsim-nek
# nincs futasidejű kapcsolója ennek kikapcsolasara, mert a mezo a geometria
# resze, nem a futtatasi beallitasoke. Ezert egy szurt compact XML-t
# generalunk, amiben ezek az include-ok hianyoznak, es azt adjuk at a
# --compactFile parameterkent. Az eredeti (kozos, tobbnyire irasvedett)
# compact fajlokat sosem modositjuk.
# ---------------------------------------------------------------------------
FIELD_INCLUDE_PATTERN = re.compile(r'<include\s+ref="[^"]*compact/fields/[^"]*"\s*/?>')


def strip_field_includes(xml_path: Path) -> str:
    """
    Beolvassa a megadott compact XML-t, es eltavolitja azokat a sorokat,
    amelyek "compact/fields/..." alatti fajlt includalnak (pl. szolenoid,
    nyalabvezetek-magnesek). Visszaadja a szurt XML tartalmat szovegkent.
    """
    text = xml_path.read_text()
    lines = text.splitlines(keepends=True)
    filtered = [line for line in lines if not FIELD_INCLUDE_PATTERN.search(line)]
    removed = len(lines) - len(filtered)
    if removed == 0:
        print(
            f"[FIGYELEM] --no-field van megadva, de nem talaltam "
            f"'compact/fields/...' include-ot ebben a fajlban: {xml_path}. "
            f"Lehet, hogy ez a compact fajl mar eleve mezo nelkuli, vagy mas "
            f"utvonalmintat hasznal.",
            file=sys.stderr,
        )
    else:
        print(f"[no-field] {removed} field-include sor kiszurve ebbol: {xml_path}")
    return "".join(filtered)


def make_no_field_compact_file(original_path: str, cache_dir: Path) -> str:
    """
    Legyartja (vagy - ha mar letezik es friss - ujrahasznositja) az eredeti
    compact XML mezo nelkuli valtozatat egy sajat, irhato cache konyvtarban,
    es visszaadja az uj fajl eleresi utjat. Az eredeti fajlt nem modositja.
    """
    original = Path(original_path)
    if not original.exists():
        raise RuntimeError(f"A compact fajl nem talalhato: {original}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{original.stem}_nofield.xml"

    # Ujragyartas, ha meg nem letezik, vagy ha az eredeti fajl ujabb nala
    # (pl. valaki frissitette a detektor-telepitest).
    if not out_path.exists() or original.stat().st_mtime > out_path.stat().st_mtime:
        filtered_text = strip_field_includes(original)
        out_path.write_text(filtered_text)

    return str(out_path)


@dataclass(frozen=True)
class Job:
    particle: str
    detector: str          # "standalone" | "fulldet"
    num_events: int
    seed_index: int         # 1-based sorszam (ez kerul a fajlnevbe, NEM a seed erteke)
    seed_value: int


def format_momentum(value: float) -> str:
    """
    Fajlnevbe irhato alak: eltavolitja a tizedespontot.
    0    -> "0"
    20   -> "20"
    0.5  -> "05"
    1.25 -> "125"
    """
    value = float(value)
    if value.is_integer():
        return str(int(value))
    s = f"{value:g}"          # pl. "0.5", "1.25" - felesleges nullak nelkul
    return s.replace(".", "").replace("-", "m")


def generate_seeds(num_seeds: int, all_random: bool) -> list[int]:
    """
    Alapertelmezetten: az elso ket seed mindig 42 es 1024, a tobbi
    determinisztikus (egyszeruen novekvo egesz szamok: 3, 4, 5, ...).
    Ha all_random=True, MINDEN seed (az elso ketto is) veletlenszeru lesz.
    """
    if all_random:
        return [random.randint(1, 2_147_483_647) for _ in range(num_seeds)]

    fixed = [42, 1024]
    seeds = []
    for i in range(num_seeds):
        seeds.append(fixed[i] if i < len(fixed) else i + 1)
    return seeds


def build_basename(file_name: str, detector: str, num_events: int,
                    mom_min: float, mom_max: float, seed_index: int,
                    name_tag: str = "") -> str:
    mom_tag = f"M{format_momentum(mom_min)}_{format_momentum(mom_max)}GeV"
    tag = f"_{name_tag}" if name_tag else ""
    return f"{file_name}_{detector}_N{num_events}_{mom_tag}_reg{tag}_s{seed_index}"


def resolve_name_tag(args: argparse.Namespace) -> str:
    """
    A fajlnevbe kerulo extra cimke. Alapertelmezesben "truth", ha --mc-truth
    aktiv (hogy a ket adathalmaz ne keveredjen ossze), egyebkent ures.
    A --name-tag felulirja; --name-tag "" kikapcsolja.
    """
    if args.name_tag is not None:
        return args.name_tag.strip().strip("_")
    return "truth" if args.mc_truth else ""


def resolve_converter(args: argparse.Namespace) -> Path:
    """
    Melyik ROOT->CSV konverter fusson. --converter auto eseten a
    reszecske-informacios valtozat, ha --mc-truth aktiv, kulonben az
    eredeti better_root_to_csv.py.
    """
    choice = args.converter
    if choice == "auto":
        choice = "particles" if args.mc_truth else "basic"
    path = Path(args.better_root_to_csv if choice == "basic"
                else args.root_to_csv_with_particles).resolve()
    if not path.exists():
        raise RuntimeError(f"A konverter script nem talalhato: {path}")
    return path


def get_compact_file(detector: str, args: argparse.Namespace) -> str:
    detector_path = os.environ.get("DETECTOR_PATH")
    if not detector_path:
        raise RuntimeError("A DETECTOR_PATH kornyezeti valtozo nincs beallitva.")
    if detector == "standalone":
        compact_file = f"{detector_path}/epic_lfhcal_only.xml"
    else:
        detector_config = os.environ.get("DETECTOR_CONFIG")
        if not detector_config:
            raise RuntimeError(
                "A DETECTOR_CONFIG kornyezeti valtozo nincs beallitva (fulldet-hez szukseges)."
            )
        compact_file = f"{detector_path}/{detector_config}.xml"

    if args.no_field:
        cache_dir = Path(args.roots_dir) / "_nofield_compact"
        compact_file = make_no_field_compact_file(compact_file, cache_dir)

    return compact_file


def build_job_list(args: argparse.Namespace) -> list[Job]:
    seeds = generate_seeds(args.num_seeds, args.random_seeds)
    jobs = []
    for particle, detector, num_events in itertools.product(
        args.particles, args.detector, args.events
    ):
        for seed_index, seed_value in enumerate(seeds, start=1):
            jobs.append(Job(particle, detector, num_events, seed_index, seed_value))
    return jobs


def run_one_job(job: Job, args: argparse.Namespace) -> str:
    info = PARTICLES[job.particle]
    basename = build_basename(
        info["file_name"], job.detector, job.num_events,
        args.momentum_min, args.momentum_max, job.seed_index,
        resolve_name_tag(args),
    )

    work_root = Path(args.work_dir) if args.work_dir else Path(args.roots_dir) / "_work"
    work_dir = work_root / basename
    work_dir.mkdir(parents=True, exist_ok=True)
    success = False

    try:
        (work_dir / "steering.py").write_text(STEERING_TEMPLATE.format(
            theta_min=args.theta_min,
            theta_max=args.theta_max,
            momentum_min=args.momentum_min,
            momentum_max=args.momentum_max,
            num_events=job.num_events,
            particle=info["gun_name"],
            seed=job.seed_value,
            physics_list=args.physics_list,
            steering_extras=build_steering_extras(args),
        ))

        compact_file = get_compact_file(job.detector, args)

        subprocess.run(
            ["ddsim", "--steeringFile", "steering.py",
             "--compactFile", compact_file, "-G"],
            cwd=work_dir, check=True,
        )

        sim_root = work_dir / "sim.root"
        if not sim_root.exists():
            raise RuntimeError("ddsim nem hozta letre a sim.root fajlt.")

        # A .root-ot MAR ITT a vegleges helyere tesszuk. Igy egy elszallo
        # konverzio nem viszi magaval a leszimulalt esemenyeket: a ROOT
        # megmarad, es kesobb kezzel ujra lehet konvertalni.
        roots_dir = Path(args.roots_dir)
        roots_dir.mkdir(parents=True, exist_ok=True)
        final_root = roots_dir / f"{basename}.root"
        shutil.move(str(sim_root), str(final_root))

        # A ket konverternek mas a CLI-je: a better_root_to_csv.py mindig az
        # input melle ir, a root_to_csv_with_particles.py tud --output-dir-t.
        converter = resolve_converter(args)
        if converter.name == "better_root_to_csv.py":
            subprocess.run([sys.executable, str(converter), str(final_root)],
                           check=True)
            produced_csv = final_root.with_suffix(".csv")
        else:
            subprocess.run(
                [sys.executable, str(converter), str(final_root),
                 "--output-dir", str(work_dir), "--output-file", "sim.csv",
                 "--contributions", args.contributions],
                check=True,
            )
            produced_csv = work_dir / "sim.csv"

        if not produced_csv.exists():
            raise RuntimeError(f"{converter.name} nem hozta letre a CSV fajlt: "
                               f"{produced_csv}")

        csv_dir = Path(args.output_dir) / f"regression-{info['family']}"
        csv_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced_csv), str(csv_dir / f"{basename}.csv"))

        success = True
        return basename
    finally:
        if success:
            shutil.rmtree(work_dir, ignore_errors=True)
        else:
            # Hiba eseten meghagyjuk a steering.py-t, hogy lassa az ember,
            # mivel futott a ddsim. A .root mar a roots-dir-ben van.
            print(f"[INFO] Hiba utan a work-dir megmaradt: {work_dir}",
                  file=sys.stderr)


def parse_args(argv=None) -> argparse.Namespace:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config")
    config_options, _ = config_parser.parse_known_args(raw_argv)

    p = argparse.ArgumentParser(
        description="Parhuzamos ddsim szimulacio + ROOT->CSV konverzio, regresszios adathalmazokhoz."
    )
    p.add_argument("--config", type=Path,
                   help="JSON konfiguracios fajl; az explicit parancssori opciok felulirjak.")
    p.add_argument("--particles", nargs="+", choices=list(PARTICLES.keys()),
                    help="pl. e- mu- proton pi0 pi+ kaon+")
    p.add_argument("--num-seeds", type=int, default=1,
                    help="Hany kulon szimulacio (seed) fusson particanik/eventszamonkent/detektoronkent.")
    p.add_argument("--random-seeds", action="store_true",
                    help="Ha meg van adva, MINDEN seed veletlenszeru (az elso ketto -42, 1024- helyett is).")
    p.add_argument("--events", type=int, nargs="+",
                    help="Egy vagy tobb esemenyszam; tobbre az osszes kombinacio lefut.")
    p.add_argument("--theta-min", type=float)
    p.add_argument("--theta-max", type=float)
    p.add_argument("--momentum-min", type=float)
    p.add_argument("--momentum-max", type=float)
    p.add_argument("--detector", nargs="+", choices=["standalone", "fulldet"],
                    default=["standalone"])
    p.add_argument("--no-field", action="store_true",
                    help="A magneses (es nyalabvezetek-) mezot kihagyja a compact "
                         "geometriabol. Ehhez egy szurt compact XML-t general "
                         "(<roots-dir>/_nofield_compact ala), az eredeti compact "
                         "fajlokat nem modositja.")
    p.add_argument("--physics-list", default="FTFP_BERT",
                    help="SIM.physicsList erteke (alap: FTFP_BERT, ami a ddsim "
                         "alapertelmezese is - itt explicit kikerul a steering "
                         "fajlba, hogy a futas reprodukalhato legyen).")
    p.add_argument("--mc-truth", action="store_true",
                    help="Hitenkenti reszecske-szarmazas mentese: kikapcsolja a "
                         "tracking-terfogat alapu MC-truth ritkitast "
                         "(SIM.part.userParticleHandler = \"\"), tagabb "
                         "saveProcesses listat es allithato energiavagast hasznal. "
                         "Ez kell ahhoz, hogy a masodlagosok (es a szulo/leany "
                         "kapcsolataik) egyaltalan bekeruljenek a ROOT fajlba. "
                         "Ilyenkor alapbol a root_to_csv_with_particles.py fut "
                         "konverterkent, es a fajlnev '_truth' cimket kap.")
    p.add_argument("--keep-all-particles", action="store_true",
                    help="SIM.part.keepAllParticles = True, azaz MINDEN Geant4 "
                         "track bekerul az MCParticles-be. Implikalja a "
                         "--mc-truth kapcsolot. FIGYELEM: hadronzaporoknal ez "
                         "nagysagrendekkel megnoveli a ROOT/CSV fajlmeretet.")
    p.add_argument("--min-kinetic-energy", type=float, default=1.0,
                    help="SIM.part.minimalKineticEnergy MeV-ben (alap: 1.0). "
                         "Csak --mc-truth eseten hasznaljuk. Kisebb ertek tobb "
                         "lagy masodlagost tart meg.")
    p.add_argument("--save-processes", nargs="+",
                    default=["Decay", "conv", "hadElastic", "neutronInelastic",
                             "protonInelastic", "pi+Inelastic", "pi-Inelastic"],
                    help="SIM.part.saveProcesses lista (alap: Decay conv es a "
                         "fontosabb hadronikus folyamatok). Csak --mc-truth eseten.")
    p.add_argument("--detailed-shower", action="store_true",
                    help="SIM.enableDetailedShowerMode = True (HitCreationMode=2). "
                         "Enelkul a CaloHitContribution.PDG / stepPosition / "
                         "stepLength mezok uresek (0) maradnak, mert a "
                         "Geant4Output2EDM4hep csak DETAILED_MODE-ban tolti ki "
                         "oket. A szulo/leany informaciohoz NEM kell - azt a "
                         "contribution -> MCParticle relacio adja.")
    p.add_argument("--converter", choices=["auto", "basic", "particles"], default="auto",
                    help="Melyik ROOT->CSV konverter fusson. auto (alap): "
                         "particles, ha --mc-truth aktiv, kulonben basic.")
    p.add_argument("--contributions", choices=["earliest", "dominant", "all"],
                    default="earliest",
                    help="A particles konverter contribution modja; az 'all' "
                         "kulon sort ir minden hithez tartozo Geant4 contributionrol.")
    p.add_argument("--name-tag", default=None,
                    help="Extra cimke a fajlnevben a '_reg' utan. Alap: 'truth', "
                         "ha --mc-truth aktiv, egyebkent semmi. Ures stringgel "
                         "kikapcsolhato.")
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="Legfeljebb ennyi parhuzamos folyamat; a tenyleges "
                         "szamot a jobok szama korlatozza.")
    p.add_argument("--output-dir", default=str(Path.home() / "data" / "simulations"),
                    help="Ide kerulnek a vegleges CSV-k (regression-<particle> almappakban).")
    p.add_argument("--roots-dir", default=str(Path.home() / "data" / "roots"),
                    help="Ide kerulnek MINDEN .root fajl, egy kozos, lapos konyvtarba.")
    p.add_argument("--work-dir", default=None,
                    help="Scratch konyvtar a ddsim futtatasahoz (alap: <roots-dir>/_work).")
    p.add_argument("--better-root-to-csv",
                    default=str(Path(__file__).resolve().parent / "better_root_to_csv.py"),
                    help="Alapertelmezes: ugyanaz a mappa, ahol ez a script van.")
    p.add_argument("--root-to-csv-with-particles",
                    default=str(Path(__file__).resolve().parent / "root_to_csv_with_particles.py"),
                    help="A reszecske-informacios konverter utvonala. "
                         "Alapertelmezes: ugyanaz a mappa, ahol ez a script van.")
    p.add_argument("--dry-run", action="store_true",
                    help="Csak kilistazza a tervezett job-okat (fajlneveket), nem futtat semmit.")

    config_path = config_options.config
    if config_path:
        try:
            config = json.loads(Path(config_path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            p.error(f"Nem olvashato konfiguracios fajl ({config_path}): {exc}")
        if not isinstance(config, dict):
            p.error("A konfiguracios fajl legfelso szintjen JSON objektumnak kell lennie.")

        actions = {action.dest: action for action in p._actions}
        option_config = {
            key: value for key, value in config.items()
            if not key.startswith("_comment")
        }
        unknown = sorted(set(option_config) - (set(actions) - {"help", "config"}))
        if unknown:
            p.error(f"Ismeretlen konfiguracios kulcs(ok): {', '.join(unknown)}")

        converted = {}
        for key, value in option_config.items():
            action = actions[key]
            is_list = (
                action.nargs in ("+", "*")
                or (isinstance(action.nargs, int) and action.nargs > 0)
            )
            values = value if is_list else [value]
            if is_list and not isinstance(value, list):
                p.error(f"A '{key}' konfiguracios ertekenek listanak kell lennie.")
            if not is_list and isinstance(value, (dict, list)):
                p.error(f"A '{key}' konfiguracios erteke nem lehet objektum vagy lista.")
            try:
                if action.type:
                    values = [action.type(item) for item in values]
                if action.choices:
                    invalid = [item for item in values if item not in action.choices]
                    if invalid:
                        p.error(f"Ervenytelen '{key}' ertek(ek): {invalid}")
                if isinstance(action, argparse._StoreTrueAction):
                    if not isinstance(value, bool):
                        p.error(f"A '{key}' konfiguracios ertekenek logikai erteknek kell lennie.")
                    converted[key] = value
                else:
                    converted[key] = values if is_list else values[0]
            except (TypeError, ValueError) as exc:
                p.error(f"Ervenytelen '{key}' konfiguracios ertek: {exc}")

        p.set_defaults(**converted)

    args = p.parse_args(raw_argv)
    required = ("particles", "events", "theta_min", "theta_max",
                "momentum_min", "momentum_max")
    missing = [name.replace("_", "-") for name in required
               if getattr(args, name) is None]
    if missing:
        p.error("Kotelezo beallitas hianyzik (CLI opcioban vagy configban): "
                + ", ".join(missing))
    if args.jobs < 1:
        p.error("--jobs legalabb 1 kell legyen.")
    if (args.contributions != "earliest"
            and (args.converter == "basic"
                 or (args.converter == "auto"
                     and not (args.mc_truth or args.keep_all_particles)))):
        p.error("--contributions csak a particles konverterrel hasznalhato.")

    if config_path:
        base_dir = Path(config_path).expanduser().resolve().parent
        for name in ("output_dir", "roots_dir", "work_dir"):
            value = getattr(args, name)
            if value is not None:
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = base_dir / path
                setattr(args, name, str(path.resolve()))
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    # --keep-all-particles onmagaban ertelmetlen a truth-beallitasok nelkul.
    if args.keep_all_particles:
        args.mc_truth = True

    jobs = build_job_list(args)
    name_tag = resolve_name_tag(args)

    if args.dry_run:
        modes = []
        if args.no_field:
            modes.append("--no-field: mezo nelkul")
        if args.mc_truth:
            modes.append(
                "--mc-truth"
                + (" + keepAllParticles" if args.keep_all_particles else "")
                + f", minKinE={args.min_kinetic_energy} MeV"
            )
        if args.detailed_shower:
            modes.append("--detailed-shower")
        mode = f" [{'; '.join(modes)}]" if modes else ""
        print(f"{len(jobs)} job keszulne el (dry-run, semmi nem fut le){mode}:\n")
        for job in jobs:
            info = PARTICLES[job.particle]
            basename = build_basename(
                info["file_name"], job.detector, job.num_events,
                args.momentum_min, args.momentum_max, job.seed_index, name_tag,
            )
            print(f"  regression-{info['family']}/{basename}.csv   (seed={job.seed_value})")
        print(f"\nKonverter: {resolve_converter(args)}")
        extras = build_steering_extras(args)
        if extras:
            print("\nA steering fajlba kerulo extra beallitasok:")
            print(extras)
        return 0

    # Korai hiba, ha a valasztott konverter nincs a helyen - jobb most
    # elszallni, mint 1000 esemeny leszimulalasa utan.
    converter = resolve_converter(args)
    print(f"Konverter: {converter}")

    if args.no_field:
        # A szurt compact fajlokat a fo folyamatban generaljuk le elore,
        # hogy a parhuzamos workerek ne probaljak ugyanazt a fajlt egyszerre
        # (verseny nelkul) letrehozni.
        print("--no-field: mezo nelkuli compact XML-ek elokeszitese...")
        for detector in sorted({job.detector for job in jobs}):
            path = get_compact_file(detector, args)
            print(f"  {detector}: {path}")

    if args.mc_truth:
        print(
            "--mc-truth: userParticleHandler kikapcsolva, "
            f"keepAllParticles={bool(args.keep_all_particles)}, "
            f"minimalKineticEnergy={args.min_kinetic_energy} MeV, "
            f"saveProcesses={list(args.save_processes)}"
        )

    worker_count = min(args.jobs, len(jobs))
    print(f"{len(jobs)} szimulacio indul, parhuzamossag: {worker_count} "
          f"(keret: {args.jobs})")
    ok, failed = 0, []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(run_one_job, job, args): job for job in jobs}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Szimulaciok"):
            job = futures[future]
            try:
                basename = future.result()
                ok += 1
                tqdm.write(f"[OK]   {basename}") if hasattr(tqdm, "write") else print(f"[OK]   {basename}")
            except Exception as exc:  # noqa: BLE001 - jelentjuk, nem allitjuk le a tobbit
                failed.append((job, str(exc)))
                print(f"[HIBA] {job}: {exc}", file=sys.stderr)

    print(f"\nKesz: {ok}/{len(jobs)} sikeres.")
    if failed:
        print("Sikertelen job-ok:", file=sys.stderr)
        for job, err in failed:
            print(f"  {job}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
