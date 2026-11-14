import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_simulations as runner


class NoFieldGeometryTests(unittest.TestCase):
    def test_preserves_beamline_constants_and_removes_nested_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'detector'
            (root / 'compact' / 'fields').mkdir(parents=True)
            original = '<lccdd><define><include ref="${DETECTOR_PATH}/compact/fields/beamline.xml"/></define><include ref="${DETECTOR_PATH}/compact/magnets.xml"/></lccdd>'
            (root / 'epic.xml').write_text(original)
            (root / 'standalone.xml').write_text('<lccdd/>')
            (root / 'compact/fields/beamline.xml').write_text('<define><constant name="offset_OMD_x" value="0"/></define>')
            (root / 'compact/magnets.xml').write_text('<lccdd><detectors><detector name="magnet"/></detectors><fields><field name="B"/></fields></lccdd>')
            cache = Path(tmp) / 'cache'
            with patch.dict(os.environ, {'DETECTOR_PATH': str(root)}):
                full = Path(runner.make_no_field_compact_file(str(root / 'epic.xml'), cache))
                mirror = Path(os.environ['DETECTOR_PATH'])
                self.assertEqual(full, mirror / 'epic.xml')
                self.assertEqual(full.read_text(), original)
                self.assertIn('offset_OMD_x', (mirror / 'compact/fields/beamline.xml').read_text())
                geometry = ET.parse(mirror / 'compact/magnets.xml').getroot()
                self.assertIsNone(geometry.find('fields'))
                self.assertIsNotNone(geometry.find('detectors/detector'))
                second = runner.make_no_field_compact_file(str(mirror / 'standalone.xml'), cache)
                self.assertEqual(second, str(mirror / 'standalone.xml'))
                self.assertEqual(os.environ['DETECTOR_CACHE'], str(mirror))
            self.assertIn('<fields>', (root / 'compact/magnets.xml').read_text())
            self.assertEqual((root / 'epic.xml').read_text(), original)

    def test_rejects_cache_inside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'epic.xml'
            source.write_text('<lccdd/>')
            with patch.dict(os.environ, {'DETECTOR_PATH': str(root)}):
                with self.assertRaisesRegex(RuntimeError, 'outside DETECTOR_PATH'):
                    runner.make_no_field_compact_file(str(source), root / 'cache')
