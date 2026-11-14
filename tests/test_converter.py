import csv
import sys
from pathlib import Path
import tempfile
import unittest

import awkward as ak
import uproot

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import root_to_csv_with_particles as converter

LEGACY_HEADER = 'event_number,cellID,x,y,z,energy,earliest_contribution_time,incoming_momentum,incoming_theta,incoming_phi,n_contributions,contribution_index,contrib_pdg,contrib_type,contrib_energy,contrib_energy_fraction,contrib_time,particle_index,particle_pdg,particle_type,is_secondary,parent_pdg,parent_type,daughter_pdg,daughter_type'.split(',')


class LegacyCSVTests(unittest.TestCase):
    def test_legacy_schema_and_earliest_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.root'
            values = {
                'LFHCALHits.cellID': [11, 22, 33],
                'LFHCALHits.energy': [10., 5., 0.],
                'LFHCALHits.position.x': [1., 2., 3.],
                'LFHCALHits.position.y': [4., 5., 6.],
                'LFHCALHits.position.z': [7., 8., 9.],
                'LFHCALHits.contributions_begin': [0, 2, 3],
                'LFHCALHits.contributions_end': [2, 3, 3],
                'LFHCALHitsContributions.PDG': [2212, 2112, 22],
                'LFHCALHitsContributions.energy': [6., 4., 5.],
                'LFHCALHitsContributions.time': [8., 2., 3.],
                '_LFHCALHitsContributions_particle.index': [0, 1, 2],
                'MCParticles.PDG': [2212, 2112, 22],
                'MCParticles.generatorStatus': [1, 0, 0],
                'MCParticles.momentum.x': [0., 0., 0.],
                'MCParticles.momentum.y': [0., 0., 0.],
                'MCParticles.momentum.z': [5., 1., 1.],
                'MCParticles.parents_begin': [0, 0, 1],
                'MCParticles.parents_end': [0, 1, 2],
                '_MCParticles_parents.index': [0, 1],
                'MCParticles.daughters_begin': [0, 1, 2],
                'MCParticles.daughters_end': [1, 2, 2],
                '_MCParticles_daughters.index': [1, 2],
            }
            arrays = {k: ak.Array([v]) for k, v in values.items()}
            with uproot.recreate(path) as root:
                root.mktree('events', arrays)
            for mode, expected in [('earliest', 3), ('all', 4)]:
                name = mode + '.csv'
                converter.root_to_csv(str(path), output_dir=tmp, output_file=name,
                                      contributions=mode, quiet=True)
                with (Path(tmp) / name).open() as stream:
                    reader = csv.DictReader(stream)
                    self.assertEqual(reader.fieldnames, LEGACY_HEADER)
                    rows = list(reader)
                self.assertEqual(len(rows), expected)
                if mode == 'earliest':
                    first = rows[0]
                    self.assertEqual(first['n_contributions'], '2')
                    self.assertEqual(first['contribution_index'], '1')
                    self.assertEqual(float(first['energy']), 10.)
                    self.assertEqual(float(first['contrib_energy']), 4.)
                    self.assertEqual(float(first['contrib_energy_fraction']), .4)
                    self.assertEqual(first['contrib_pdg'], '2112')
                    self.assertEqual(first['contrib_type'], 'neutron')
                    self.assertEqual(float(first['contrib_time']), 2.)
                    self.assertEqual(first['parent_pdg'], '2212')
                    self.assertEqual(first['parent_type'], 'proton')
                    self.assertEqual(first['daughter_pdg'], '22')
                    self.assertEqual(first['daughter_type'], 'gamma')
                    self.assertEqual(rows[1]['contribution_index'], '0')
                    self.assertEqual(rows[2]['n_contributions'], '0')
                    self.assertEqual(rows[2]['contribution_index'], '')
                    self.assertEqual(rows[2]['contrib_energy'], '')
