"""Industrial HMI for read-only production inspection replay."""
from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import logging
import time
import numpy as np
from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)
from src.ui.preview_images import prepare_pose_images, prepare_live_overlay
from src.ui.live_preview import PreviewReader
from src.ui.plane_assets import CycleObjectAsset
from src.ui.current_object import current_object_ply
from src.ui.inspection_presenter import display_judgement, load_inspection_view
from src.ui.run_replay import REPLAY_STAGES, ReplayCursor
from src.ui.theme import COLORS, STYLESHEET
from src.ui.widgets.image_panel import ImagePanel
from src.ui.widgets.pointcloud_view import PointCloudView
from src.ui.widgets.result_panel import ResultPanel


class Metric(QFrame):
    def __init__(self, title):
        super().__init__(); self.setObjectName("metric"); self.setMaximumHeight(72)
        name = QLabel(title); name.setObjectName("panelTitle")
        self.value = QLabel("--"); self.value.setObjectName("metricValue")
        box = QVBoxLayout(self); box.setContentsMargins(10, 5, 10, 5); box.addWidget(name); box.addWidget(self.value)


class IndustrialDashboard(QMainWindow):
    def __init__(self, run_dir: str | Path, *, watch=False, watch_root=False,
                 preview_channel=None, mode_3d="depth", profile=None, object_ply=None):
        super().__init__(); self.run_dir = Path(run_dir); self.watch = watch
        self.watch_root = Path(run_dir) if watch_root else None
        self.mode_3d = mode_3d
        self.profile = profile
        self.object_ply = object_ply
        self.object_asset = CycleObjectAsset()
        self.preview_reader = None
        self.preview_timestamp = 0.0
        self.preview_sequence = 0
        self.preview_metadata = {}
        self.preview_bgr = None
        self.displayed_preview = None
        self.overlay_future = None
        self.overlay_key = None
        self.overlay_refresh = 0.0
        self.image_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ui-images")
        self.image_future = None
        self.image_request = None
        self.image_pending = None
        self.image_generation = 0
        if preview_channel is not None:
            try:
                self.preview_reader = PreviewReader(preview_channel)
            except Exception:
                logging.warning('[UI WARNING] preview unavailable')
        self.setWindowTitle("Active Vision Surface Inspection"); self.resize(1920, 1080)
        self.setMinimumSize(1600, 900); self.setStyleSheet(STYLESHEET)
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 8, 12, 8); outer.setSpacing(7)
        self._header(outer); self._stages(outer); self._metrics(outer)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self._main_tab(); self._technical_tab(); self._system_bar(outer)
        self.replay = ReplayCursor(); self.clock = QElapsedTimer(); self.replay_timer = QTimer(self)
        self.replay_timer.timeout.connect(self._replay_tick); self.elapsed_before_play = 0.0
        self.reveal = 6; self.reload()
        if watch:
            self.controls.hide(); self.live_timer = QTimer(self); self.live_timer.timeout.connect(self.reload)
            self.live_timer.start(1000); self._set_stage(0)
        else: self._restart()
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._preview_tick)
        self.preview_timer.start(100)

    def _header(self, outer):
        frame = QFrame(); frame.setObjectName("header"); row = QHBoxLayout(frame)
        brand = QLabel("ACTIVE VISION  SURFACE INSPECTION"); brand.setObjectName("brand")
        self.header_info = QLabel(); self.header_info.setObjectName("runLabel")
        row.addWidget(brand); row.addStretch(); row.addWidget(self.header_info); outer.addWidget(frame)

    def _stages(self, outer):
        row = QHBoxLayout(); self.stage_labels = []
        for name in REPLAY_STAGES:
            label = QLabel(name); label.setAlignment(Qt.AlignCenter); label.setObjectName("stagePending")
            row.addWidget(label); self.stage_labels.append(label)
        outer.addLayout(row)

    def _metrics(self, outer):
        row = QHBoxLayout(); self.status = Metric("INSPECTION"); self.score = Metric("ANOMALY")
        self.pose = Metric("PLATFORM"); self.transport = Metric("TRANSPORT")
        for widget in (self.status, self.score, self.pose, self.transport): row.addWidget(widget)
        outer.addLayout(row)

    def _main_tab(self):
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(6, 6, 6, 6)
        left = QFrame(); left.setObjectName("imagePanel"); left_box = QVBoxLayout(left)
        title = QHBoxLayout(); name = QLabel("LIVE INSPECTION"); name.setObjectName("panelTitle")
        self.roi_badge = QLabel("ROI ACTIVE"); self.roi_badge.setStyleSheet(f"color:{COLORS['normal']};font-weight:700")
        self.plane_badge = QLabel("PLANE -- / --")
        title.addWidget(name); title.addWidget(self.roi_badge); title.addStretch(); title.addWidget(self.plane_badge)
        self.live_view = ImagePanel(""); left_box.addLayout(title); left_box.addWidget(self.live_view, 1)
        right = QWidget(); right_box = QVBoxLayout(right); right_box.setContentsMargins(0, 0, 0, 0)
        self.pointcloud = PointCloudView(self.mode_3d); self.localization = ImagePanel("ANOMALY LOCALIZATION")
        self.anomaly_level = QLabel("ANOMALY LEVEL   -- x TH"); self.anomaly_level.setObjectName("metricValue")
        self.anomaly_values = QLabel("MAX SCORE --     THRESHOLD --"); self.anomaly_values.setObjectName("technicalLabel")
        anomaly = QFrame(); anomaly.setObjectName("imagePanel"); anomaly_box = QVBoxLayout(anomaly)
        anomaly_box.addWidget(self.localization, 1); anomaly_box.addWidget(self.anomaly_level); anomaly_box.addWidget(self.anomaly_values)
        self.result_panel = ResultPanel()
        right_box.addWidget(self.pointcloud, 35); right_box.addWidget(anomaly, 40); right_box.addWidget(self.result_panel, 25)
        layout.addWidget(left, 59); layout.addWidget(right, 41); self.tabs.addTab(page, "INSPECTION")

    def _technical_tab(self):
        page = QWidget(); layout = QVBoxLayout(page); toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("POSE")); self.selector = QComboBox(); self.selector.currentIndexChanged.connect(self._show_pose)
        self.debug = QCheckBox("SHOW FULL PATCH GRID"); self.debug.toggled.connect(self._show_pose)
        toolbar.addWidget(self.selector); toolbar.addWidget(self.debug); toolbar.addStretch(); layout.addLayout(toolbar)
        grid = QGridLayout(); names = ("RGB RAW", "DEPTH PROJECTION", "INSPECTION MASK", "SURFACE PATCH OVERLAY",
            "ANOMALY RAW HEATMAP", "ANOMALY OVERLAY", "BOARD PLANE OVERLAY", "STRUCTURED LIGHT / PLY")
        self.detail_panels = [ImagePanel(name) for name in names]
        for i, panel in enumerate(self.detail_panels): grid.addWidget(panel, i // 4, i % 4)
        layout.addLayout(grid, 1); self.technical_text = QLabel(); self.technical_text.setObjectName("technicalLabel")
        self.technical_text.setTextInteractionFlags(Qt.TextSelectableByMouse); layout.addWidget(self.technical_text)
        self.controls = QFrame(); controls = QHBoxLayout(self.controls); controls.addWidget(QLabel("REPLAY CONTROLS"))
        self.play_button = QPushButton("PLAY"); self.pause_button = QPushButton("PAUSE"); self.restart_button = QPushButton("RESTART")
        self.speed = QComboBox(); self.speed.addItems(("1x", "2x")); self.play_button.clicked.connect(self._play)
        self.pause_button.clicked.connect(self._pause); self.restart_button.clicked.connect(self._restart)
        for widget in (self.play_button, self.pause_button, self.restart_button, self.speed): controls.addWidget(widget)
        controls.addStretch(); layout.addWidget(self.controls); self.tabs.addTab(page, "TECHNICAL DETAIL")

    def _system_bar(self, outer):
        frame = QFrame(); frame.setObjectName("header"); row = QHBoxLayout(frame)
        for name in ("CAMERA", "DEPTH", "STRUCTURED LIGHT", "LIGHTING", "PLATFORM", "CONVEYOR"):
            label = QLabel(f"{name}  ●"); label.setStyleSheet(f"color:{COLORS['normal']}"); row.addWidget(label)
        row.addStretch(); self.progress = QProgressBar(); self.progress.setFixedWidth(220); row.addWidget(self.progress)
        outer.addWidget(frame)

    def _speed(self): return 2.0 if self.speed.currentText() == "2x" else 1.0
    def _play(self): self.clock.restart(); self.replay_timer.start(50)
    def _pause(self):
        if self.replay_timer.isActive(): self.elapsed_before_play += self.clock.elapsed() / 1000 * self._speed()
        self.replay_timer.stop()
    def _restart(self):
        self.replay.restart(); self.reveal = -1; self.elapsed_before_play = 0.0
        self.clock.start(); self.replay_timer.start(50); self._set_stage(0); self._show_pose(self.selector.currentIndex())
    def _replay_tick(self):
        for _ in self.replay.advance(self.elapsed_before_play + self.clock.elapsed() / 1000 * self._speed()):
            self.reveal = self.replay.index; self._set_stage(self.reveal); self._show_pose(self.selector.currentIndex())
        if self.replay.index == len(self.replay.events) - 1: self.replay_timer.stop()
    def _set_stage(self, active):
        for i, label in enumerate(self.stage_labels):
            label.setObjectName("stageDone" if i < active else "stageActive" if i == active else "stagePending")
            label.setText(("✓ " if i < active else "") + REPLAY_STAGES[i]); label.style().unpolish(label); label.style().polish(label)
        self.progress.setMaximum(6); self.progress.setValue(max(0, active))

    def show_final_state(self):
        """Render the completed replay frame for deterministic screenshots."""
        self.replay_timer.stop(); self.reveal = 6; self._set_stage(6)
        self._show_pose(self.selector.currentIndex())

    def reload(self):
        if self.watch_root is not None:
            runs = sorted(self.watch_root.glob("run_*"))
            if runs and runs[-1] != self.run_dir:
                self.run_dir = runs[-1]
                self.pointcloud.set_data(None, None, 'WAITING FOR 3D SCAN', cycle=self.run_dir)
                self.image_generation += 1
                self.image_pending = None
                if hasattr(self, 'view'):
                    del self.view
        canonical = current_object_ply(self.run_dir)
        selected = self.object_asset.select(self.run_dir, self.profile, self.object_ply, canonical)
        stage = self.preview_metadata.get('stage', '')
        message = ('STRUCTURED LIGHT SCANNING / 3D MODEL GENERATING'
                   if stage in ('STRUCTURED_LIGHT_SCAN', 'STRUCTURED_LIGHT') else 'WAITING FOR 3D SCAN')
        self.pointcloud.set_data(selected, None, 'DEBUG OBJECT OVERRIDE' if self.object_asset.reference else message,
                                 cycle=self.run_dir)
        try: self.view = load_inspection_view(self.run_dir)
        except (FileNotFoundError, ValueError) as exc:
            self.status.value.setText("N/A"); self.technical_text.setText(str(exc)); return
        digits = "".join(c for c in self.view.run_name if c.isdigit()); cycle = f"#{int(digits[-3:]):03d}" if digits else "#001"
        self.header_info.setText(f"SYSTEM READY  ●     PART: {self.view.product}     CYCLE: {cycle}     MODE: AUTO")
        if self.watch and self.view.finished_at:
            self._set_stage(6)
        self.status.value.setText(self.view.status); self.transport.value.setText("OUT COMPLETE" if self.view.transport_complete else "--")
        old = self.selector.currentIndex(); labels = [f"PLANE {p.index + 1} / {len(self.view.poses)}  ·  {p.name}" for p in self.view.poses]
        self.selector.blockSignals(True); self.selector.clear(); self.selector.addItems(labels)
        self.selector.setCurrentIndex(max(0, min(old, len(labels) - 1))); self.selector.blockSignals(False); self._show_pose(self.selector.currentIndex())

    def _show_pose(self, index=0):
        if not hasattr(self, "view") or not (0 <= index < len(self.view.poses)): return
        pose = self.view.poses[index]; total = len(self.view.poses); state = display_judgement(pose.judgement)
        relative = None if pose.score is None or not pose.threshold else pose.score / pose.threshold
        self.score.value.setText("--" if relative is None else f"{relative:.2f}x TH")
        self.pose.value.setText(f"R {pose.roll or 0:+.1f}°   P {pose.pitch or 0:+.1f}°   Z {pose.z or 0:.1f}cm")
        self.plane_badge.setText(f"PLANE {index + 1} / {total}")
        self.anomaly_level.setText("ANOMALY LEVEL   --" if relative is None else f"ANOMALY LEVEL   {relative:.2f}x TH")
        self.anomaly_values.setText(f"MAX SCORE  {pose.score:.6f}     THRESHOLD  {pose.threshold:.6f}" if pose.score is not None and pose.threshold is not None else "MAX SCORE --     THRESHOLD --")
        self.result_panel.set_result(state, visible=self.reveal >= 5)
        info = f"PART: {(self.profile or self.view.product).upper()}    POSE {index + 1} / {total}    ROLL {pose.roll or 0:+.1f}°    PITCH {pose.pitch or 0:+.1f}°    Z {pose.z or 0:.1f} cm"
        selected_ply = self.object_asset.select(self.run_dir, self.profile, self.object_ply, self.view.ply)
        static_asset = self.object_asset.reference
        self.image_generation += 1
        self.image_pending = (self.image_generation, pose, self.reveal, selected_ply,
                              info + f"    {self.view.stage}" + ("    STATIC OBJECT ASSET" if static_asset else ""),
                              self.preview_sequence)
        self._start_image_work()
        self.technical_text.setText(f"RUN: {self.view.run_dir}\nCYCLE RESULT: {self.view.run_dir / 'cycle_result.json'}\nPLY: {self.view.ply or 'N/A'}\nSCORE: {pose.score if pose.score is not None else '--'}   THRESHOLD: {pose.threshold if pose.threshold is not None else '--'}   STATUS: {pose.status}")

    def _start_image_work(self):
        if self.image_future is None and self.image_pending is not None:
            self.image_request = self.image_pending
            self.image_pending = None
            _, pose, reveal, _, _, _ = self.image_request
            self.image_future = self.image_worker.submit(prepare_pose_images, pose, reveal)

    def _display_preview(self, array):
        if array is None and self.displayed_preview is None:
            return
        if array is not None and self.displayed_preview is not None:
            if np.array_equal(array, self.displayed_preview):
                return
        self.displayed_preview = array
        self.live_view.set_array(array)

    def _preview_tick(self):
        # OpenCV and file decoding happen only in the worker. No queued frame backlog.
        if self.image_future is not None and self.image_future.done():
            generation, _, reveal, ply, info, sequence = self.image_request
            try:
                live, localization, depth, details = self.image_future.result()
                if generation == self.image_generation:
                    if (self.preview_reader is None or
                            (sequence == self.preview_sequence and
                             time.time() - self.preview_timestamp > .5)):
                        if live is not None or self.preview_reader is None:
                            self._display_preview(live)
                    self.localization.set_array(localization)
                    self.pointcloud.set_data(ply if reveal >= 1 else None,
                                             depth if reveal >= 1 else None, info, cycle=self.run_dir)
                    for panel, array in zip(self.detail_panels, details):
                        panel.set_array(array)
            except Exception:
                logging.warning('[UI WARNING] preview unavailable')
            self.image_future = None
            self._start_image_work()
        if self.preview_reader is None:
            return
        if getattr(getattr(self, 'view', None), 'finished_at', None):
            self.live_view.title.setText('FINAL RESULT')
            return
        try:
            overlay_applied = False
            metadata = self.preview_reader.read_metadata()
            if metadata is not None:
                self.preview_metadata = metadata
            key = (self.preview_sequence, self.preview_metadata.get('pose_index', -1), self.run_dir)
            if self.overlay_future is not None and self.overlay_future.done():
                try:
                    if self.overlay_key == key:
                        self._display_preview(self.overlay_future.result())
                        overlay_applied = True
                finally:
                    self.overlay_future = None
            newest = self.preview_reader.read_latest()
            if newest is not None:
                rgb, metadata = newest
                self.preview_sequence = metadata['sequence']
                self.preview_timestamp = metadata['timestamp']
                # Existing ImagePanel takes BGR; conversion is just a NumPy copy.
                self.preview_bgr = rgb[..., ::-1].copy()
                if not overlay_applied:
                    self._display_preview(self.preview_bgr)
            if (self.overlay_future is None and self.preview_bgr is not None and
                    (newest is not None or time.monotonic() - self.overlay_refresh >= 1)):
                self.overlay_key = (self.preview_sequence,
                                    self.preview_metadata.get('pose_index', -1), self.run_dir)
                self.overlay_future = self.image_worker.submit(
                    prepare_live_overlay, self.run_dir, self.overlay_key[1], self.preview_bgr)
                self.overlay_refresh = time.monotonic()
            waiting = time.time() - self.preview_timestamp > 1.0
            stage = self.preview_metadata.get('stage') or getattr(getattr(self, 'view', None), 'stage', '')
            if waiting and stage in ('STRUCTURED_LIGHT_SCAN', 'STRUCTURED_LIGHT'):
                stage = '3D SCAN IN PROGRESS'
            label = '● HOLD' if waiting else '● LIVE'
            self.live_view.title.setText(f'{label}  {stage}')
            if not hasattr(self, 'view'):
                index = self.preview_metadata.get('pose_index', -1)
                if self.profile or self.object_ply or index >= 0:
                    info = f"PART: {(self.profile or 'UNKNOWN').upper()}    POSE {index + 1 if index >= 0 else '--'} / --    R {self.preview_metadata.get('roll')}    P {self.preview_metadata.get('pitch')}    Z {self.preview_metadata.get('z')}"
                    selected = self.object_asset.select(self.run_dir, self.profile, self.object_ply, current_object_ply(self.run_dir))
                    self.pointcloud.set_data(selected, None, info + f'    {stage}' + ('    STATIC OBJECT ASSET' if self.object_asset.reference else '    DEPTH FALLBACK'), cycle=self.run_dir)
                self.status.value.setText(stage or 'WAITING')
                active = {
                    'STRUCTURED_LIGHT_SCAN': 1, 'STRUCTURED_LIGHT': 1,
                    'METRIC_POSE': 2, 'PLAN_DOMINANT_POSE': 2,
                    'MOVE_SAFE_Z': 2, 'MOVE_ORIENTATION': 2,
                    'AUTOMATIC_Z': 3, 'FINAL_GEOMETRY_CAPTURE': 3,
                    'FINAL_RGB_CAPTURE': 3, 'ANOMALY_INFERENCE': 4,
                    'READY_FOR_ANOMALY': 4, 'CONVEYOR_OUT': 6,
                    'COMPLETE': 6, 'PARTIAL_COMPLETE': 6,
                }.get(stage)
                if active is not None and self.progress.value() != active:
                    self._set_stage(active)
        except Exception:
            logging.warning('[UI WARNING] preview unavailable')
            self.preview_reader.close()
            self.preview_reader = None

    def closeEvent(self, event):
        self.pointcloud.shutdown()
        self.preview_timer.stop()
        self.replay_timer.stop()
        if hasattr(self, 'live_timer'):
            self.live_timer.stop()
        self.image_worker.shutdown(wait=False, cancel_futures=True)
        if self.preview_reader is not None:
            self.preview_reader.close()
            self.preview_reader = None
        super().closeEvent(event)
