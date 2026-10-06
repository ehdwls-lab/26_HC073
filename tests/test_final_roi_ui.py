import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import cv2
import numpy as np
from src.ui.inspection_presenter import load_inspection_view
from src.ui.preview_images import final_roi_overlay, prepare_live_overlay

class FinalROIUITests(unittest.TestCase):
    def test_accepted_metadata_wins_over_candidate_surface_and_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mask = np.zeros((80,128), np.uint8); mask[10:70,10:118] = 255
            cv2.imwrite(str(root/'accepted.png'), mask)
            for name in ('candidate', 'surface', 'patch'):
                cv2.imwrite(str(root/f'{name}.png'), np.zeros_like(mask))
            (root/'cycle_result.json').write_text(json.dumps({'inspection_planes':[{
                'inspection_mask_path':'candidate.png', 'final_rgb_path':'old.png',
                'anomaly_result':{'metadata':{'inspection_mask_path':'accepted.png',
                    'rgb_path':'final.png', 'inspection_area_px':6480,
                    'surface_mask_path':'surface.png', 'surface_patch_overlay_path':'patch.png',
                    'anomaly_roi_type':'depth_rgb_seeded_fallback'}}}]}))
            pose = load_inspection_view(root).poses[0]
            self.assertEqual(pose.mask, root/'accepted.png')
            self.assertEqual(pose.rgb, root/'final.png')
            self.assertEqual(int(np.count_nonzero(cv2.imread(str(pose.mask),0))), pose.inspection_area_px)
            self.assertEqual(pose.roi_type, 'depth_rgb_seeded_fallback')

    def test_fill_normal_red_defect_alignment_and_live_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rgb = np.full((80,128,3), 80, np.uint8)
            mask = np.zeros((80,128), np.uint8); mask[10:70,10:118] = 255
            cv2.imwrite(str(root/'mask.png'), mask)
            pose = SimpleNamespace(mask=root/'mask.png', overlay=None, inspection_area_px=6480, roi_type='inspection_mask')
            with self.assertLogs(level='INFO') as logs:
                normal = final_roi_overlay(rgb, pose)
            self.assertIn('inspection_px=6480', str(logs.output))
            self.assertGreater(normal[40,64,1], rgb[40,64,1])
            self.assertTrue(np.array_equal(normal[0,0],rgb[0,0]))
            red = rgb.copy(); red[30:40,50:60] = (0,0,255)
            cv2.imwrite(str(root/'red.png'), red); pose.overlay=root/'red.png'
            defect = final_roi_overlay(rgb, pose)
            self.assertTrue(np.all(defect[35,55] == (0,0,255)))
            self.assertGreater(defect[50,80,1],rgb[50,80,1])
            self.assertTrue(np.array_equal(prepare_live_overlay(root,0,rgb),rgb))
            self.assertTrue(np.array_equal(final_roi_overlay(rgb[:40],pose),rgb[:40]))
