"""Isolated PyVista rendering with a depth fallback, independent of live RGB."""
from pathlib import Path
import sys
import tempfile
import json
from collections import deque

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QPushButton, QHBoxLayout

from src.ui.widgets.object_image_panel import ObjectImagePanel


class PointCloudView(QWidget):
    def __init__(self, mode='depth'):
        super().__init__()
        self.mode = mode
        self.interactor = None  # No native OpenGL context inside the live dashboard.
        self.title = QLabel('3D OBJECT / INSPECTION VIEW'); self.title.setObjectName('panelTitle')
        self.model_status = QLabel('WAITING FOR 3D SCAN')
        self.info = QLabel('3D DATA NOT AVAILABLE'); self.info.setObjectName('technicalLabel')
        self.fallback = ObjectImagePanel()
        self.fallback.interaction.connect(self.send_interaction)
        self.reset_button = QPushButton('RESET VIEW')
        self.reset_button.setMaximumWidth(110)
        self.reset_button.clicked.connect(lambda: self.send_interaction({'event': 'reset'}))
        self.reset_button.setEnabled(False)
        box = QVBoxLayout(self)
        header = QHBoxLayout(); header.addWidget(self.title); header.addStretch(); header.addWidget(self.reset_button)
        box.addLayout(header); box.addWidget(self.model_status); box.addWidget(self.info); box.addWidget(self.fallback, 1)
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.readyReadStandardOutput.connect(self._ready)
        self.process.errorOccurred.connect(self._error)
        self.timeout = QTimer(self); self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.process.kill)
        self.temporary = tempfile.TemporaryDirectory(prefix='inspection-ply-')
        self.output = Path(self.temporary.name) / 'plane.png'
        self.cycle = None
        self.commands = deque(maxlen=32)
        self.busy = True
        self.stdout_buffer = b""
        self.requested = None
        self.depth_image = None
        self.rendered = None

    def set_data(self, ply: Path | None, depth_image, info: str, *, cycle=None):
        if cycle != self.cycle:
            self._cancel()
            self.requested = None
            self.cycle = cycle
            self.rendered = None
            self.fallback.set_array(None)
            self.model_status.setText("WAITING FOR 3D SCAN")
        self.info.setText(info)
        self.depth_image = depth_image
        if self.mode != 'ply' or ply is None or not ply.is_file():
            self._cancel()
            self.requested = None
            self.rendered = None
            self.fallback.set_array(depth_image)
            self.model_status.setText("STRUCTURED LIGHT SCANNING / 3D MODEL GENERATING" if "SCANNING" in info else "WAITING FOR 3D SCAN / 3D DATA NOT AVAILABLE")
            return
        if self.requested == ply:
            if self.rendered is None and self.process.state() == QProcess.NotRunning:
                self.fallback.set_array(depth_image)
            return
        self._cancel()
        self.model_status.setText("LOADING 3D MODEL")
        self.requested = ply
        self.rendered = None
        self.fallback.set_array(depth_image)
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[3]))
        self.process.start(sys.executable, ['-m', 'src.ui.render_ply', '--input', str(ply),
                                          '--output', str(self.output), '--interactive'])
        self.timeout.start(30000)

    def send_interaction(self, command):
        if self.rendered is None or self.process.state() != QProcess.Running:
            return
        if command['event'] == 'MouseMoveEvent' and self.commands and self.commands[-1]['event'] == 'MouseMoveEvent':
            self.commands[-1] = command
        else:
            self.commands.append(command)
        self._send_next()

    def _send_next(self):
        if not self.busy and self.commands:
            command = self.commands.popleft()
            self.busy = True
            self.process.write((json.dumps(command) + '\n').encode())
            self.timeout.start(30000)

    def _ready(self):
        self.stdout_buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or not message.get('frame'):
                    continue
                pixmap = QPixmap()
                pixmap.loadFromData(self.output.read_bytes())
                if pixmap.isNull():
                    continue
                self.model_status.setText("● 3D MODEL READY")
                self.rendered = pixmap
                self.fallback._pixmap = pixmap
                self.fallback._rescale()
                self.reset_button.setEnabled(True)
                self.timeout.stop()
                self.busy = False
                self._send_next()
            except (ValueError, OSError):
                self._error(None)

    def _finished(self, code, status):
        self._error(None)

    def _error(self, error):
        self.model_status.setText("3D DATA NOT AVAILABLE / DEPTH FALLBACK")
        self.timeout.stop()
        self.reset_button.setEnabled(False)
        self.commands.clear()
        # Preserve an available static render; otherwise use depth.
        if self.rendered is None:
            self.fallback.set_array(self.depth_image)

    def _cancel(self):
        self.timeout.stop()
        self.reset_button.setEnabled(False)
        self.commands.clear()
        self.stdout_buffer = b""
        self.busy = True
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)

    def shutdown(self):
        self._cancel()
        self.temporary.cleanup()
