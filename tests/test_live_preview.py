from __future__ import annotations

import ast
import io
from contextlib import redirect_stdout
import multiprocessing as mp
from multiprocessing import shared_memory
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.ui.live_preview import PreviewChannel, PreviewReader, SharedMemoryPreviewPublisher
from src.tools.run_inspection_with_ui import build_parser, production_arguments, supervise


def publish_mock(channel, count=20):
    publisher = SharedMemoryPreviewPublisher(channel, fps=1000)
    try:
        for i in range(count):
            publisher.publish_rgb(np.full((40, 60, 3), i, dtype=np.uint8))
            time.sleep(.003)
    finally:
        publisher.close()


def crash_reader(channel):
    reader = PreviewReader(channel)
    channel.locks[0].acquire()
    os._exit(17)


def attach_and_exit(channel):
    reader = PreviewReader(channel)
    reader.close()


class PreviewTransportTests(unittest.TestCase):
    def setUp(self):
        self.context = mp.get_context('spawn')
        self.channel = PreviewChannel.create(self.context, capacity=1280 * 800 * 3)
        self.publisher = SharedMemoryPreviewPublisher(self.channel)
        self.reader = PreviewReader(self.channel)

    def tearDown(self):
        self.reader.close()
        self.publisher.close()
        self.channel.unlink()

    def test_shape_color_sequence_and_no_repeat(self):
        frame = np.zeros((800, 1280, 3), dtype=np.uint8)
        frame[..., 0] = 211
        self.publisher.publish_rgb(frame)
        frame[:] = 0
        rgb, metadata = self.reader.read_latest()
        self.assertEqual(rgb.shape, (800, 1280, 3))
        self.assertTrue(np.all(rgb[..., 0] == 211))
        self.assertEqual(metadata['sequence'], 1)
        self.assertGreater(metadata['timestamp'], 0)
        self.assertIsNone(self.reader.read_latest())

    def test_latest_overwrites_and_slow_reader_never_blocks(self):
        self.publisher.interval = 0
        frame = np.zeros((12, 20, 3), np.uint8)
        start = time.monotonic()
        for i in range(200):
            frame[:] = i
            self.publisher.publish_rgb(frame)
        self.assertLess(time.monotonic() - start, 2)
        latest, metadata = self.reader.read_latest()
        self.assertEqual(metadata['sequence'], 200)
        self.assertTrue(np.all(latest == 199))

    def test_busy_slots_drop_without_waiting_and_resume(self):
        for lock in self.channel.locks:
            lock.acquire()
        try:
            start = time.monotonic()
            self.publisher.publish_rgb(np.zeros((10, 10, 3), np.uint8))
            self.assertLess(time.monotonic() - start, .2)
            self.assertEqual(self.publisher.sequence, 0)
        finally:
            for lock in self.channel.locks:
                lock.release()
        self.publisher.publish_rgb(np.zeros((10, 10, 3), np.uint8))
        self.assertIsNotNone(self.reader.read_latest())

    def test_one_stranded_slot_does_not_stop_publishing(self):
        self.channel.locks[0].acquire()
        try:
            self.publisher.interval = 0
            for i in range(4):
                self.publisher.publish_rgb(np.full((10, 10, 3), i, np.uint8))
            frame, metadata = self.reader.read_latest()
            self.assertEqual(metadata['sequence'], 4)
            self.assertTrue(np.all(frame == 3))
        finally:
            self.channel.locks[0].release()

    def test_cross_process_consistency(self):
        child = self.context.Process(target=publish_mock, args=(self.channel, 100))
        child.start()
        sequences = []
        deadline = time.monotonic() + 10
        try:
            while child.is_alive() and time.monotonic() < deadline:
                result = self.reader.read_latest()
                if result:
                    frame, metadata = result
                    self.assertTrue(np.all(frame == frame.flat[0]))
                    sequences.append(metadata['sequence'])
            child.join(2)
            self.assertEqual(child.exitcode, 0)
            self.assertTrue(sequences)
            self.assertEqual(sequences, sorted(set(sequences)))
        finally:
            if child.is_alive():
                child.terminate()
            child.join()

    def test_reader_process_exit_does_not_unlink_shared_memory(self):
        child = self.context.Process(target=attach_and_exit, args=(self.channel,))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 0)
        self.publisher.publish_rgb(np.ones((10, 10, 3), np.uint8))
        self.assertIsNotNone(self.reader.read_latest())

    def test_abrupt_reader_crash_does_not_block_production(self):
        child = self.context.Process(target=crash_reader, args=(self.channel,))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 17)
        self.publisher.interval = 0
        for i in range(10):
            self.publisher.publish_rgb(np.full((10, 10, 3), i, np.uint8))
        frame, metadata = self.reader.read_latest()
        self.assertEqual(metadata['sequence'], 10)
        self.assertTrue(np.all(frame == 9))

    def test_mock_ten_fps_throttle(self):
        frame = np.zeros((10, 10, 3), np.uint8)
        with patch('src.ui.live_preview.time.monotonic') as clock:
            for i in range(100):
                clock.return_value = i * .01
                self.publisher.publish_rgb(frame)
        self.assertGreaterEqual(self.publisher.sequence, 9)
        self.assertLessEqual(self.publisher.sequence, 10)

    def test_metadata_updates_without_camera_frames(self):
        self.publisher.update_metadata(stage='AUTOMATIC_Z', pose_index=2, roll=-3, pitch=4, z=25)
        self.assertEqual(self.reader.read_metadata(), dict(stage='AUTOMATIC_Z', pose_index=2,
                                                         roll=-3, pitch=4, z=25))
        self.assertIsNone(self.reader.read_latest())

    def test_invalid_frame_does_not_corrupt_previous_frame(self):
        self.publisher.interval = 0
        self.publisher.publish_rgb(np.ones((10, 10, 3), np.uint8))
        for frame in (np.zeros((10, 10), np.uint8), np.zeros((10, 10, 3)),
                      np.zeros((900, 1280, 3), np.uint8)):
            with self.assertRaises(ValueError):
                self.publisher.publish_rgb(frame)
        self.assertTrue(np.all(self.reader.read_latest()[0] == 1))

    def test_explicit_unlink(self):
        channel = PreviewChannel.create(self.context, capacity=100)
        channel.unlink()
        channel.unlink()
        with self.assertRaises(FileNotFoundError):
            shared_memory.SharedMemory(name=channel.name)


class PreviewIntegrationTests(unittest.TestCase):
    def test_existing_capture_once_and_failure_isolation(self):
        from src.camera.orbbec_controller import OrbbecCameraController
        bgr = np.zeros((4, 5, 3), np.uint8); bgr[..., 0] = 255
        depth = np.ones((4, 5))
        sink = Mock()
        camera = OrbbecCameraController(preview_sink=sink)
        camera._pipeline = object(); camera._align_filter = object()
        helper = SimpleNamespace(wait_for_aligned_pair=Mock(return_value=(bgr, depth)))
        with patch.dict(sys.modules, {'src.test_surface_only_pose_inspection': helper}):
            captured = camera.capture()
            helper.wait_for_aligned_pair.assert_called_once()
            self.assertIs(captured.color_bgr, bgr)
            self.assertIs(captured.depth_mm, depth)
            self.assertTrue(np.all(sink.publish_rgb.call_args.args[0][..., 2] == 255))
            sink.publish_rgb.side_effect = RuntimeError('broken preview')
            self.assertIs(camera.capture().color_bgr, bgr)

    def test_startup_warmup_reuses_exact_acquisitions(self):
        from src.camera.orbbec_controller import OrbbecCameraController
        sink = Mock()
        sink.publish_rgb.side_effect = RuntimeError('preview unavailable')
        camera = OrbbecCameraController(warmup_frames=3, preview_sink=sink)
        sdk = Mock()
        helper = Mock()
        helper.wait_for_aligned_pair.return_value = (np.zeros((4, 5, 3), np.uint8), np.ones((4, 5)))
        with patch.dict(sys.modules, {'pyorbbecsdk': sdk,
                                     'src.test_surface_only_pose_inspection': helper}):
            camera.start()
            self.assertEqual(helper.wait_for_aligned_pair.call_count, 3)
            self.assertEqual(sink.publish_rgb.call_count, 3)
            sdk.Pipeline.return_value.start.assert_called_once()
            camera.close()
            sdk.Pipeline.return_value.stop.assert_called_once()

    def test_stage_observer_failure_isolation(self):
        from src.integration.integrated_inspection_cycle import IntegratedInspectionCycle, IntegratedCycleResult
        from src.integration.integrated_inspection_cycle import IntegratedCycleStage
        cycle = object.__new__(IntegratedInspectionCycle)
        cycle.camera = SimpleNamespace(preview_sink=Mock())
        cycle.camera.preview_sink.update_metadata.side_effect = RuntimeError('preview lost')
        result = IntegratedCycleResult('/tmp/test', 25, 25)
        cycle._stage(result, IntegratedCycleStage.AUTOMATIC_Z)
        self.assertEqual(result.stage, 'AUTOMATIC_Z')

    def test_ui_has_no_hardware_imports(self):
        paths = list(Path('src/ui').rglob('*.py')) + [Path('src/tools/run_inspection_ui.py')]
        for path in paths:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or '']
                for name in names:
                    self.assertFalse(name.startswith(('pyorbbecsdk', 'src.camera', 'serial',
                                                      'src.platform', 'src.lighting', 'src.conveyor')), path)

    def test_profiles_reuse_production_validation_and_fixed_mapping(self):
        from src.tools.test_integrated_inspection_cycle import build_parser as production_parser, _validate_static
        for profile in ('gray', 'blue'):
            args = build_parser().parse_args(['--profile', profile])
            parsed = production_parser().parse_args(production_arguments(args, Path('/tmp/preview-test')))
            _validate_static(parsed)
            expected_model, expected_manifest = {
                'gray': ('models/gray_v2/best_autoencoder.pth', 'data/manifests/gray_v2/val.csv'),
                'blue': ('models/blue_v1/best_autoencoder.pth', 'data/manifests/blue_v1_rot180/val.csv'),
            }[profile]
            root = Path(__file__).resolve().parents[1]
            self.assertEqual(parsed.anomaly_model, root / expected_model)
            self.assertEqual(parsed.anomaly_val_manifest, root / expected_manifest)
            self.assertEqual((parsed.scan_z, parsed.safe_z, parsed.z_start, parsed.z_max), (0, 15, 25, 25))
            self.assertEqual((parsed.cover_open_angle, parsed.cover_close_angle, parsed.cover_cleanup_state), (90, 0, 'CLOSE'))
            self.assertTrue(parsed.platform_port.startswith('/dev/serial/by-id/'))
            self.assertEqual((parsed.z_start, parsed.z_search_min, parsed.z_coarse_step), (25, 17, 1))
            self.assertFalse(parsed.execute)
            self.assertEqual(args.ui_3d_mode, 'depth')

    def test_launcher_matches_verified_production_cli(self):
        from src.tools.test_integrated_inspection_cycle import build_parser as production_parser, _validate_static
        from src.tools.run_inspection_with_ui import ROOT, PORTS
        profiles = []
        for profile in ('gray', 'blue'):
            args = build_parser().parse_args(['--profile', profile])
            mapped = production_parser().parse_args(production_arguments(args, ROOT / 'results/integrated_hardware'))
            # Explicit, hardware-verified invocation; all unspecified settings are production defaults.
            reference = production_parser().parse_args([
                '--conveyor-port', PORTS[0], '--platform-port', PORTS[1], '--lighting-port', PORTS[2],
                '--conveyor-steps', '6325', '--cover-open-angle', '90', '--cover-close-angle', '0',
                '--monitor', 'HDMI-0', '--scan-z', '0', '--safe-z', '15',
                '--z-start', '25', '--z-search-min', '17', '--z-max', '25',
                '--z-coarse-step', '1', '--z-fine-step', '1',
                '--pose-plan-mode', 'all_valid_planes',
                '--quality-config', str(ROOT / 'config/automatic_z_quality.json'),
                '--anomaly-model', str(mapped.anomaly_model),
                '--anomaly-val-manifest', str(mapped.anomaly_val_manifest),
            ])
            _validate_static(mapped); _validate_static(reference)
            self.assertEqual(vars(mapped), vars(reference))
            self.assertEqual((mapped.conveyor_steps, mapped.conveyor_out_steps,
                              mapped.platform_motion_timeout, mapped.anomaly_surface_coverage),
                             (6325, 10000, 30, 1.0))
            profiles.append({k: v for k, v in vars(mapped).items()
                             if k not in ('anomaly_model', 'anomaly_val_manifest')})
        self.assertEqual(profiles[0], profiles[1])

    def test_safe_z_override_is_independent_of_scan_and_search_start(self):
        from src.tools.test_integrated_inspection_cycle import build_parser as production_parser, _validate_static
        for profile in ('gray', 'blue'):
            args = build_parser().parse_args(['--profile', profile, '--safe-z', '20'])
            parsed = production_parser().parse_args(production_arguments(args, Path('/tmp/preview-test')))
            _validate_static(parsed)
            self.assertEqual((parsed.scan_z, parsed.safe_z, parsed.z_start), (0, 20, 25))

    def test_both_profile_dry_run_summaries(self):
        from src.tools.run_inspection_with_ui import main
        for profile in ('gray', 'blue'):
            output = io.StringIO()
            with redirect_stdout(output), patch('src.tools.run_inspection_with_ui.mp.get_context') as spawn:
                self.assertEqual(main(['--profile', profile]), 0)
            spawn.assert_not_called()
            self.assertIn('structured-light scan/reference Z: 0.0', output.getvalue())
            self.assertIn('safe Z: 15.0', output.getvalue())
            self.assertIn('adaptive Z: start=25.0, max=25.0, min=17.0, coarse_step=1.0, fine_step=1.0',
                          output.getvalue())
            self.assertIn('DRY RUN', output.getvalue())

    def test_invalid_scan_z_fails_before_session_or_process_creation(self):
        from src.tools.run_inspection_with_ui import main
        for execute in ([], ['--execute']):
            for profile in ('gray', 'blue'):
                with patch('src.tools.run_inspection_with_ui.mp.get_context') as spawn, \
                     patch('src.tools.run_inspection_with_ui.tempfile.mkdtemp') as session:
                    with self.assertRaisesRegex(ValueError, 'production structured-light scan_z must be 0'):
                        main(['--profile', profile, '--scan-z', '25'] + execute)
                spawn.assert_not_called()
                session.assert_not_called()

    def test_constructor_still_rejects_scan_z_25(self):
        from src.integration.integrated_inspection_cycle import IntegratedInspectionCycle
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / 'must_not_be_created'
            with self.assertRaisesRegex(ValueError, 'production structured-light scan_z must be 0'):
                IntegratedInspectionCycle(
                    conveyor=None, structured_light_runner=None, pose_planner=None,
                    projector=None, platform=None, motion_diagnostic=None, camera=None,
                    automatic_z_search=None, scan_z=25, safe_z=15, run_directory=run_dir,
                )
            self.assertFalse(run_dir.exists())

    def test_ui_closed_does_not_terminate_production(self):
        production = Mock(exitcode=0)
        ui = Mock(exitcode=1)
        self.assertEqual(supervise(production, ui), 0)
        production.join.assert_called_once()
        production.terminate.assert_not_called()
        production.kill.assert_not_called()
        ui.join.assert_called_once()

    def test_actual_ui_exit_while_production_continues_and_no_orphans(self):
        context = mp.get_context('spawn')
        channel = PreviewChannel.create(context, capacity=40 * 60 * 3)
        production = context.Process(target=publish_mock, args=(channel, 150))
        ui = context.Process(target=attach_and_exit, args=(channel,))
        production.start(); ui.start()
        try:
            ui.join(10)
            self.assertEqual(ui.exitcode, 0)
            self.assertEqual(supervise(production, ui), 0)
            reader = PreviewReader(channel)
            try:
                self.assertEqual(reader.read_latest()[1]['sequence'], 150)
            finally:
                reader.close()
            self.assertFalse(production.is_alive())
            self.assertFalse(ui.is_alive())
        finally:
            for process in (production, ui):
                if process.is_alive():
                    process.terminate()
                process.join()
            channel.unlink()

    def test_current_pose_overlays_without_patch_grid(self):
        import cv2
        from src.ui.preview_images import prepare_live_overlay
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / 'plane_00/final_capture'
            anomaly = root / 'plane_00/anomaly'
            final.mkdir(parents=True); anomaly.mkdir()
            rgb = np.zeros((40, 60, 3), np.uint8)
            mask = np.zeros((40, 60), np.uint8); mask[10:30, 10:50] = 255
            cv2.imwrite(str(final / 'inspection_mask.png'), mask)
            cv2.imwrite(str(anomaly / 'surface_patch_overlay.png'), np.full_like(rgb, 255))
            outlined = prepare_live_overlay(root, 0, rgb)
            self.assertTrue(np.array_equal(outlined, rgb))
            self.assertTrue(np.array_equal(prepare_live_overlay(root, 1, rgb), rgb))
            red = rgb.copy(); red[15:25, 20:30, 2] = 255
            cv2.imwrite(str(anomaly / 'anomaly_overlay.png'), red)
            result = prepare_live_overlay(root, 0, rgb)
            self.assertEqual(result[20, 25, 2], 0)
            self.assertEqual(result[0, 0, 0], 0)

    def test_launcher_cleans_channel_when_ui_start_fails(self):
        from src.tools.run_inspection_with_ui import main
        context = Mock()
        production = Mock(pid=123, exitcode=0)
        ui = Mock()
        ui.start.side_effect = RuntimeError('Qt unavailable')
        context.Process.side_effect = [ui, production]
        channel = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with patch('src.tools.run_inspection_with_ui.mp.get_context', return_value=context), \
                 patch('src.tools.run_inspection_with_ui.PreviewChannel.create', return_value=channel), \
                 patch('src.tools.run_inspection_with_ui.DupFd'), \
                 patch('src.tools.run_inspection_with_ui.sys.stdin'):
                code = main(['--profile', 'gray', '--execute', '--output-root', directory])
        self.assertEqual(code, 0)
        production.start.assert_called_once()
        production.terminate.assert_not_called()
        channel.unlink.assert_called_once()

    def test_offscreen_early_resize_and_preview_ui(self):
        script = '''
import multiprocessing as mp
import sys
import tempfile
import time
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from pathlib import Path
import cv2
from src.ui.widgets.image_panel import ImagePanel
from src.ui.industrial_dashboard import IndustrialDashboard
from src.ui.live_preview import PreviewChannel, SharedMemoryPreviewPublisher
app = QApplication([])
partial = ImagePanel.__new__(ImagePanel)
QWidget.__init__(partial)
partial.resizeEvent(QResizeEvent(QSize(300, 200), QSize(0, 0)))
panel = ImagePanel('test')
panel.resize(300, 200)
panel.show()
app.processEvents()
del panel._pixmap
panel._rescale()
channel = PreviewChannel.create(mp.get_context('spawn'))
publisher = SharedMemoryPreviewPublisher(channel)
with tempfile.TemporaryDirectory() as root:
    run = Path(root) / 'run_0001'
    final = run / 'plane_00/final_capture'
    final.mkdir(parents=True)
    mask = np.zeros((80, 128), np.uint8); mask[20:60, 30:90] = 255
    cv2.imwrite(str(final / 'inspection_mask.png'), mask)
    publisher.update_metadata(stage='FINAL_RGB_CAPTURE', pose_index=0)
    saw_contour = False
    window = IndustrialDashboard(root, watch=True, watch_root=True, preview_channel=channel)
    window.show()
    assert window.pointcloud.interactor is None
    assert 'pyvistaqt' not in sys.modules
    for i in range(10):
        publisher.publish_rgb(np.full((80, 128, 3), i, np.uint8))
        until = time.monotonic() + .11
        while time.monotonic() < until:
            app.processEvents()
            if window.displayed_preview is not None:
                saw_contour |= bool(np.any(window.displayed_preview[..., 1] == 255))
            time.sleep(.002)
    assert window.preview_sequence >= 8, window.preview_sequence
    assert not saw_contour, "saved ROI leaked onto unregistered live frame"
    before = window.displayed_preview.copy()
    publisher.update_metadata(stage="STRUCTURED_LIGHT_SCAN", pose_index=-1)
    window.preview_timestamp = 0
    window._preview_tick()
    assert "● HOLD" in window.live_view.title.text()
    assert "3D SCAN IN PROGRESS" in window.live_view.title.text()
    assert np.array_equal(window.displayed_preview, before)
    assert window.live_view._pixmap is not None
    assert 'pyorbbecsdk' not in sys.modules
    assert 'src.camera.orbbec_controller' not in sys.modules
    # A renderer child exits abnormally; live UI and depth fallback remain usable.
    from PySide6.QtCore import QProcess
    cloud = window.pointcloud
    cloud.depth_image = np.full((20, 30, 3), 80, np.uint8)
    cloud.process.start(sys.executable, ['-c', 'import os; os._exit(139)'])
    deadline = time.monotonic() + 5
    while cloud.process.state() != QProcess.NotRunning and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.002)
    app.processEvents()
    assert cloud.process.state() == QProcess.NotRunning
    assert cloud.fallback._pixmap is not None
    publisher.last_publish = float('-inf')
    publisher.publish_rgb(np.full((80, 128, 3), 42, np.uint8))
    window._preview_tick()
    assert window.preview_bgr[0, 0, 0] == 42
    window.close()
publisher.close()
channel.unlink()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertNotIn('resource_tracker', result.stderr)


if __name__ == '__main__':
    unittest.main()
