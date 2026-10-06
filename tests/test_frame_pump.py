import ast
from pathlib import Path
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from src.camera.frame_pump import FramePump
from src.ui.plane_assets import (CycleObjectAsset, point_rgb, GRAY_OBJECT_PLY_DEFAULT,
                                 GRAY_OBJECT_PLY_ALTERNATE)


class FramePumpTests(unittest.TestCase):
    def test_continuous_single_owner_fresh_requests_and_shutdown(self):
        owners = set()
        count = [0]
        published = []
        def acquire(stop):
            owners.add(threading.get_ident())
            stop.wait(.005)
            count[0] += 1
            return SimpleNamespace(color_bgr=np.full((2, 3, 3), count[0]), index=count[0])
        pump = FramePump(acquire, lambda rgb: published.append(int(rgb[0, 0, 0])))
        pump.start()
        try:
            time.sleep(.03)
            self.assertGreater(len(published), 1)  # No production captures needed.
            frames = []
            for _ in range(5):
                before = pump.started
                frame = pump.capture()
                self.assertGreater(frame.index, before)
                frames.append(frame.index)
            self.assertEqual(frames, sorted(set(frames)))
            self.assertEqual(len(owners), 1)
            self.assertNotIn(threading.get_ident(), owners)
        finally:
            pump.close()
        self.assertFalse(pump.thread.is_alive())

    def test_acquisition_failure_propagates_but_preview_failure_does_not(self):
        def acquire(stop):
            stop.wait(.005)
            return SimpleNamespace(color_bgr=np.zeros((2, 2, 3)))
        def preview(rgb):
            raise ValueError('preview')
        pump = FramePump(acquire, preview)
        pump.start()
        try:
            self.assertIsNotNone(pump.capture())
        finally:
            pump.close()
        def broken(stop):
            raise ValueError('camera lost')
        pump = FramePump(broken, preview)
        pump.start()
        try:
            with self.assertRaisesRegex(ValueError, 'camera lost'):
                pump.capture()
        finally:
            pump.close()

    def test_stop_unblocks_waiting_capture(self):
        entered = threading.Event()
        def acquire(stop):
            entered.set(); stop.wait()
            raise InterruptedError('stopped')
        pump = FramePump(acquire, lambda rgb: None)
        pump.start(); self.assertTrue(entered.wait(1))
        errors = []
        def consume():
            try:
                pump.capture()
            except RuntimeError as exc:
                errors.append(str(exc))
        consumer = threading.Thread(target=consume)
        consumer.start(); pump.close(); consumer.join(1)
        self.assertFalse(consumer.is_alive())
        self.assertEqual(errors, ['camera pump stopped'])

    def test_pump_with_shared_memory_ten_fps_without_production_capture(self):
        import multiprocessing as mp
        from src.ui.live_preview import PreviewChannel, PreviewReader, SharedMemoryPreviewPublisher
        channel = PreviewChannel.create(mp.get_context('spawn'), capacity=100)
        publisher = SharedMemoryPreviewPublisher(channel)
        reader = PreviewReader(channel)
        def acquire(stop):
            stop.wait(.01)
            return SimpleNamespace(color_bgr=np.zeros((2, 3, 3), np.uint8))
        pump = FramePump(acquire, publisher.publish_rgb)
        pump.start()
        try:
            time.sleep(1.05)  # No reader and no production consumer during this interval.
            self.assertGreaterEqual(reader.read_latest()[1]['sequence'], 8)
            self.assertLessEqual(publisher.sequence, 11)
        finally:
            pump.close(); publisher.close(); reader.close(); channel.unlink()

    def test_controller_pump_keeps_warmup_and_one_pipeline(self):
        import sys
        from unittest.mock import Mock
        from src.camera.orbbec_controller import OrbbecCameraController
        sdk = Mock()
        helper = Mock()
        warmup = []
        def pair(pipeline, align, *, stop_event=None):
            if stop_event is None:
                warmup.append(1)
            else:
                stop_event.wait(.005)
            return np.zeros((2, 3, 3), np.uint8), np.zeros((2, 3), np.float32)
        helper.wait_for_aligned_pair.side_effect = pair
        camera = OrbbecCameraController(warmup_frames=3, continuous_preview=True)
        with patch.dict(sys.modules, {'pyorbbecsdk': sdk, 'src.test_surface_only_pose_inspection': helper}):
            camera.start()
            thread = camera._pump.thread
            try:
                timestamps = [camera.capture().timestamp for _ in range(4)]
                self.assertEqual(len(set(timestamps)), 4)
                self.assertEqual(len(warmup), 3)
                sdk.Pipeline.assert_called_once()
            finally:
                camera.close()
            self.assertFalse(thread.is_alive())
            sdk.Pipeline.return_value.stop.assert_called_once()

    def test_structured_light_finishes_before_integrated_camera_start(self):
        source = Path('src/integration/integrated_inspection_cycle.py').read_text()
        tree = ast.parse(source)
        run = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'run')
        scan = [node.lineno for node in ast.walk(run) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'run_scan']
        starts = [node.lineno for node in ast.walk(run) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == 'self.camera.start']
        self.assertTrue(scan and starts)
        self.assertLess(max(scan), min(starts))
        self.assertNotIn('pyorbbecsdk', Path('src/camera/frame_pump.py').read_text())
        self.assertEqual(Path('src/camera/orbbec_controller.py').read_text().count('pipeline = Pipeline()'), 1)


class PlaneAssetTests(unittest.TestCase):
    def test_gray_assets_preserved_and_blue_has_no_gray_default(self):
        for index, path in enumerate((GRAY_OBJECT_PLY_DEFAULT, GRAY_OBJECT_PLY_ALTERNATE)):
            self.assertTrue(path.is_file())
            header = path.read_text().split('end_header')[0]
            self.assertIn('property uchar red', header)
            self.assertIn(f'element vertex {19735 if index == 0 else 17532}', header)
            self.assertEqual(path.read_bytes(), (path.parent / f'inspection_plane_{index + 1:02d}.ply').read_bytes())
        self.assertIsNone(CycleObjectAsset().select('run', 'gray'))
        self.assertIsNone(CycleObjectAsset().select('run', 'blue'))

    def test_original_rgb_selected_without_colormap(self):
        rgb = np.array([[12, 34, 56]], np.uint8)
        self.assertTrue(np.array_equal(point_rgb(SimpleNamespace(point_data={'RGB': rgb})), rgb))
        fields = dict(zip(('red', 'green', 'blue'), rgb.T))
        self.assertTrue(np.array_equal(point_rgb(SimpleNamespace(point_data=fields)), rgb))
        self.assertIsNone(point_rgb(SimpleNamespace(point_data={})))

    def test_final_overlays_apply_only_to_final_image(self):
        from src.ui.preview_images import prepare_pose_images, prepare_live_overlay
        import cv2
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rgb = np.zeros((40, 60, 3), np.uint8)
            mask = np.zeros((40, 60), np.uint8); mask[5:35, 5:55] = 255
            red = rgb.copy(); red[10:20, 10:20, 2] = 255
            for name, frame in [('rgb.png', rgb), ('mask.png', mask), ('overlay.png', red)]:
                cv2.imwrite(str(root / name), frame)
            pose = SimpleNamespace(rgb=root/'rgb.png', mask=root/'mask.png', overlay=root/'overlay.png',
                                   heatmap=None, depth=None, score=None, threshold=None,
                                   patch_overlay=None, board_overlay=None)
            live, _, _, _ = prepare_pose_images(pose, 4)
            self.assertEqual(live[15, 15, 2], 255)
            self.assertTrue(np.any(live[..., 1] == 255))
            self.assertTrue(np.array_equal(prepare_live_overlay(root, 0, rgb), rgb))
