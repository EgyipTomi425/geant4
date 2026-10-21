import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_simulations


class DefaultConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = REPO_ROOT / "scripts" / "config.json"
        cls.config = json.loads(cls.config_path.read_text())
        cls.args = run_simulations.parse_args(
            ["--config", str(cls.config_path), "--dry-run"]
        )

    def test_mc_truth_uses_particle_aware_converter(self):
        converter = run_simulations.resolve_converter(self.args)
        self.assertEqual(converter.name, "root_to_csv_with_particles.py")

    def test_default_configuration_plans_four_particles_and_two_fixed_seeds(self):
        jobs = run_simulations.build_job_list(self.args)
        self.assertEqual(len(jobs), 8)
        self.assertEqual(
            [job.seed_value for job in jobs[:2]],
            [42, 1024],
        )
        self.assertEqual({job.num_events for job in jobs}, {100})
        self.assertEqual({job.detector for job in jobs}, {"standalone"})

    def test_mc_truth_and_detailed_steps_are_enabled_without_keep_all(self):
        self.assertTrue(self.args.mc_truth)
        self.assertFalse(self.args.keep_all_particles)
        self.assertTrue(self.args.detailed_shower)
        steering = run_simulations.build_steering_extras(self.args)
        self.assertIn("SIM.part.userParticleHandler = \"\"", steering)
        self.assertIn("SIM.part.keepAllParticles = False", steering)
        self.assertIn("SIM.part.minimalKineticEnergy = 1.0*MeV", steering)
        self.assertIn("SIM.enableDetailedShowerMode = True", steering)

    def test_explanatory_json_keys_are_not_runner_options(self):
        self.assertTrue(any(key.startswith("_comment") for key in self.config))
        self.assertEqual(self.args.particles, self.config["particles"])


if __name__ == "__main__":
    unittest.main()
