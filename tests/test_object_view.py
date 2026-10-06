import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.ui.plane_assets import CycleObjectAsset, GRAY_OBJECT_PLY_DEFAULT, GRAY_OBJECT_PLY_ALTERNATE


class ObjectViewTests(unittest.TestCase):
    def test_same_object_for_all_poses_new_cycle_can_change(self):
        selector = CycleObjectAsset()
        for pose in range(6):
            self.assertEqual(selector.select('run1', 'gray', current_run=GRAY_OBJECT_PLY_DEFAULT), GRAY_OBJECT_PLY_DEFAULT)
        self.assertEqual(selector.select('run1', 'blue', GRAY_OBJECT_PLY_ALTERNATE), GRAY_OBJECT_PLY_DEFAULT)
        self.assertEqual(selector.select('run2', 'gray', GRAY_OBJECT_PLY_ALTERNATE), GRAY_OBJECT_PLY_ALTERNATE)
        self.assertIsNone(selector.select('run3', 'blue'))

    def test_missing_override_and_blue_current_run_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'current.ply'
            selector = CycleObjectAsset()
            self.assertIsNone(selector.select('run', 'blue', current_run=path))
            path.touch()
            self.assertEqual(selector.select('run', 'blue', current_run=path), path)
            self.assertFalse(selector.reference)
            self.assertEqual(CycleObjectAsset().select('other', 'gray', '/missing.ply', path), path)

    def test_override_parsing_and_profile_only_changes_model(self):
        from src.tools.run_inspection_with_ui import build_parser, production_arguments
        from src.tools.run_inspection_ui import build_parser as ui_parser
        args = build_parser().parse_args(['--profile', 'gray', '--ui-object-ply', str(GRAY_OBJECT_PLY_ALTERNATE)])
        reference = build_parser().parse_args(['--profile', 'gray'])
        self.assertEqual(production_arguments(args, Path('/tmp/run')), production_arguments(reference, Path('/tmp/run')))
        ui = ui_parser().parse_args(['--run', '/tmp/run', '--profile', 'gray', '--ui-object-ply', str(args.ui_object_ply)])
        self.assertEqual(ui.ui_object_ply, GRAY_OBJECT_PLY_ALTERNATE)

    def test_launcher_forwards_profile_and_override_to_ui_only(self):
        from unittest.mock import patch
        from src.tools.run_inspection_with_ui import ui_main
        with patch('src.tools.run_inspection_with_ui.signal.signal'), \
             patch('src.tools.run_inspection_ui.main', return_value=0) as ui:
            with self.assertRaises(SystemExit):
                ui_main('/tmp/run', 'ply', None, 'gray', GRAY_OBJECT_PLY_ALTERNATE)
        arguments = ui.call_args.args[0]
        self.assertEqual(arguments[arguments.index('--profile') + 1], 'gray')
        self.assertEqual(arguments[arguments.index('--ui-object-ply') + 1], str(GRAY_OBJECT_PLY_ALTERNATE))

    def test_vtk_trackball_rotate_zoom_pan_reset(self):
        commands = [
            {'event': 'LeftButtonPressEvent', 'x': .5, 'y': .5},
            {'event': 'MouseMoveEvent', 'x': .7, 'y': .6},
            {'event': 'LeftButtonReleaseEvent', 'x': .7, 'y': .6},
            {'event': 'MouseWheelForwardEvent', 'x': .5, 'y': .5},
            {'event': 'MiddleButtonPressEvent', 'x': .5, 'y': .5},
            {'event': 'MouseMoveEvent', 'x': .6, 'y': .7},
            {'event': 'MiddleButtonReleaseEvent', 'x': .6, 'y': .7},
            {'event': 'reset'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'object.png'
            result = subprocess.run([sys.executable, '-m', 'src.ui.render_ply', '--input',
                                     str(GRAY_OBJECT_PLY_DEFAULT), '--output', str(output), '--interactive'],
                                    input=''.join(json.dumps(command) + '\n' for command in commands),
                                    capture_output=True, text=True, timeout=45,
                                    env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'MPLCONFIGDIR': directory})
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            frames = [json.loads(line)['camera'] for line in result.stdout.splitlines() if line.startswith('{')]
            self.assertEqual(len(frames), 9)
            self.assertNotEqual(frames[0], frames[2])  # rotate
            self.assertNotEqual(frames[3][0], frames[4][0])  # zoom changes position
            self.assertNotEqual(frames[4][1], frames[6][1])  # pan changes focal point
            self.assertEqual(frames[0], frames[-1])  # explicit reset restores fitted view
            self.assertGreater(output.stat().st_size, 10000)

    def test_widget_pose_metadata_does_not_restart_renderer_or_reset_view(self):
        script = '''
import sys, time, tempfile
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QProcess, Qt, QPoint
from PySide6.QtTest import QTest
from src.ui.widgets.pointcloud_view import PointCloudView
from src.ui.plane_assets import GRAY_OBJECT_PLY_DEFAULT
app = QApplication([])
view = PointCloudView('ply'); view.resize(800, 600); view.show()
def wait_until(predicate):
    deadline = time.monotonic() + 20
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert predicate()
try:
    view.set_data(GRAY_OBJECT_PLY_DEFAULT, None, 'POSE 1 / 3', cycle='one')
    wait_until(lambda: view.rendered is not None)
    pid = view.process.processId()
    original = view.rendered.toImage()
    image = view.fallback.image
    QTest.mousePress(image, Qt.LeftButton, pos=QPoint(image.width()//2, image.height()//2))
    QTest.mouseMove(image, QPoint(image.width()*3//4, image.height()*2//3))
    QTest.mouseRelease(image, Qt.LeftButton, pos=QPoint(image.width()*3//4, image.height()*2//3))
    wait_until(lambda: not view.busy and not view.commands)
    assert view.rendered.toImage() != original
    rotated = view.rendered.toImage()
    for pose in range(2, 6):
        view.set_data(GRAY_OBJECT_PLY_DEFAULT, None, f'POSE {pose} / 5', cycle='one')
        app.processEvents()
        assert view.process.processId() == pid
        assert view.rendered.toImage() == rotated
    view.reset_button.click()
    wait_until(lambda: not view.busy and not view.commands)
    assert view.rendered.toImage() == original
    view.set_data(GRAY_OBJECT_PLY_DEFAULT, None, 'POSE 1 / 2', cycle='two')
    wait_until(lambda: view.rendered is not None)
    assert view.process.processId() != pid
    with tempfile.TemporaryDirectory() as directory:
        bad = Path(directory) / 'invalid.ply'; bad.write_text('invalid PLY')
        depth = np.full((40, 60, 3), 77, np.uint8)
        view.set_data(bad, depth, 'LOAD FAILURE', cycle='three')
        wait_until(lambda: view.process.state() == QProcess.NotRunning)
        assert view.rendered is None
        assert view.fallback._pixmap is not None
        view.set_data(Path(directory) / 'missing.ply', depth, 'MISSING', cycle='four')
        assert view.process.state() == QProcess.NotRunning
        assert view.fallback._pixmap is not None
finally:
    view.shutdown(); view.close()
assert view.process.state() == QProcess.NotRunning
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                timeout=60, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
