import json
from pathlib import Path
import tempfile
import unittest
from src.ui.current_object import current_object_ply
from src.ui.plane_assets import CycleObjectAsset, GRAY_OBJECT_PLY_DEFAULT


class CurrentObjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'ui_session/run_1'
        self.sl = self.root / 'structured_light'
        self.sl.mkdir(parents=True)
        self.object = self.sl / 'raw/scan/object.ply'
        self.object.parent.mkdir(parents=True)
        self.object.write_text('ply\nformat ascii 1.0\nelement vertex 0\nend_header\n')
        self.manifest = dict(return_code=0, finished_at='completed', run_id='scan',
                             result_directory=str(self.object.parent),
                             artifacts={'integrated_object_only_ply': str(self.object)})

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        (self.sl / 'structured_light_manifest.json').write_text(json.dumps(self.manifest))

    def test_no_manifest_no_static_and_completed_canonical_only(self):
        self.assertIsNone(current_object_ply(self.root))
        for profile in ('gray', 'blue'):
            self.assertIsNone(CycleObjectAsset().select(self.root, profile))
        self.save()
        self.assertEqual(current_object_ply(self.root), self.object)
        self.manifest['artifacts']['segmented_ply'] = str(GRAY_OBJECT_PLY_DEFAULT)
        self.manifest['artifacts']['integrated_object_only_ply'] = None
        self.save()
        self.assertIsNone(current_object_ply(self.root))

    def test_relative_source_and_nested_session(self):
        self.manifest['result_directory'] = 'raw/scan'
        self.manifest['artifacts']['integrated_object_only_ply'] = 'object.ply'
        self.save()
        self.assertEqual(current_object_ply(self.root), self.object)

    def test_other_run_rejected_external_source_requires_matching_run_info(self):
        other = Path(self.temp.name) / 'other/scan'
        other.mkdir(parents=True)
        path = other / 'object.ply'; path.write_bytes(self.object.read_bytes())
        self.manifest['result_directory'] = str(other)
        self.manifest['artifacts']['integrated_object_only_ply'] = str(path)
        self.save()
        self.assertIsNone(current_object_ply(self.root))
        (self.sl / 'run_info.json').write_text(json.dumps(dict(run_id='scan', result_directory=str(other))))
        self.assertEqual(current_object_ply(self.root), path)

    def test_incomplete_corrupt_missing_no_fallback(self):
        self.manifest['finished_at'] = None; self.save()
        self.assertIsNone(current_object_ply(self.root))
        self.manifest['finished_at'] = 'done'; self.save()
        self.object.write_text('corrupt')
        self.assertIsNone(current_object_ply(self.root))
        self.object.unlink()
        self.assertIsNone(current_object_ply(self.root))

    def test_latch_pose_changes_clear_on_new_cycle_explicit_debug(self):
        self.save()
        asset = CycleObjectAsset()
        for _ in range(4):
            self.assertEqual(asset.select(self.root, 'gray', current_run=current_object_ply(self.root)), self.object)
        self.assertIsNone(asset.select(self.root.parent / 'run_2', 'gray'))
        self.assertEqual(asset.select('debug', 'blue', GRAY_OBJECT_PLY_DEFAULT), GRAY_OBJECT_PLY_DEFAULT)
