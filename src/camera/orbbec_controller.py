from __future__ import annotations

import time

from src.camera.controller import RGBDepthFrame
from src.config import InspectionConfig


class OrbbecCameraController:
    """Lifecycle wrapper around the verified Gemini 336L capture helpers.

    SDK and experimental helper imports are lazy, so mock/system imports do not
    initialize camera hardware. Surface processing remains in ``src.core``.
    """

    def __init__(self, config: InspectionConfig | None = None, warmup_frames: int = 30,
                 *, preview_sink=None, continuous_preview=False) -> None:
        self.preview_sink = preview_sink
        self.continuous_preview = continuous_preview
        self._pump = None
        self.config = config or InspectionConfig.default()
        self.warmup_frames = max(0, int(warmup_frames))
        self._pipeline = None
        self._align_filter = None

    def start(self) -> None:
        if self._pipeline is not None:
            return
        from pyorbbecsdk import AlignFilter, Config, OBFrameAggregateOutputMode, OBStreamType, Pipeline
        from src.test_surface_only_pose_inspection import (
            configure_camera, find_color_profile, find_depth_profile, wait_for_aligned_pair,
        )
        pipeline = Pipeline()
        sdk_config = Config()
        sdk_config.enable_stream(find_color_profile(pipeline))
        sdk_config.enable_stream(find_depth_profile(pipeline))
        try:
            sdk_config.set_frame_aggregate_output_mode(OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
        except Exception:
            pass
        pipeline.start(sdk_config)
        align_filter = AlignFilter(align_to_stream=OBStreamType.COLOR_STREAM)
        try:
            configure_camera(pipeline.get_device(), type("Args", (), {
                "brightness": self.config.camera.brightness,
                "exposure": self.config.camera.exposure,
                "gain": self.config.camera.gain,
                "white_balance": self.config.camera.white_balance,
                "depth_exposure": self.config.depth.exposure,
                "depth_gain": self.config.depth.gain,
                "depth_median_frames": self.config.depth.median_frames,
            })())
            for _ in range(self.warmup_frames):
                color_bgr, _ = wait_for_aligned_pair(pipeline, align_filter)
                self._publish_preview(color_bgr)
        except Exception:
            pipeline.stop()
            raise
        self._pipeline = pipeline
        self._align_filter = align_filter
        if self.continuous_preview:
            from src.camera.frame_pump import FramePump
            self._pump = FramePump(self._acquire_for_pump, self._publish_preview)
            try:
                self._pump.start()
            except Exception:
                self._pump = None
                import logging
                logging.warning('[UI WARNING] preview pump unavailable; using synchronous capture')

    def capture(self) -> RGBDepthFrame:
        if self._pipeline is None or self._align_filter is None:
            raise RuntimeError("Orbbec camera is not started")
        if self._pump is not None:
            return self._pump.capture()
        from src.test_surface_only_pose_inspection import wait_for_aligned_pair
        color_bgr, depth_mm = wait_for_aligned_pair(self._pipeline, self._align_filter)
        self._publish_preview(color_bgr)
        return RGBDepthFrame(color_bgr=color_bgr, depth_mm=depth_mm, timestamp=time.time())

    def _acquire_for_pump(self, stop_event):
        from src.test_surface_only_pose_inspection import wait_for_aligned_pair
        color, depth = wait_for_aligned_pair(self._pipeline, self._align_filter,
                                             stop_event=stop_event)
        return RGBDepthFrame(color_bgr=color.copy(), depth_mm=depth.copy(), timestamp=time.time())

    def _publish_preview(self, color_bgr) -> None:
        if self.preview_sink is not None:
            from src.ui.live_preview import publish_safely
            publish_safely(self.preview_sink, color_bgr)

    def color_intrinsics(self, width: int, height: int):
        """Return validated intrinsics for the aligned color/depth grid."""
        if self._pipeline is None:
            raise RuntimeError("Orbbec camera is not started")
        from src.integration.metric_pose import CameraIntrinsics
        from src.integration.orbbec_intrinsics import build_d2c_intrinsics_payload

        payload = build_d2c_intrinsics_payload(
            self._pipeline.get_camera_param(),
            depth_grid_width=int(width), depth_grid_height=int(height),
        )
        raw = payload["color_intrinsics"]
        return CameraIntrinsics(
            fx=float(raw["fx"]), fy=float(raw["fy"]),
            cx=float(raw["cx"]), cy=float(raw["cy"]),
            width=int(raw["width"]), height=int(raw["height"]),
            source=str(payload["intrinsic_source"]), aligned_to="color",
        )

    def close(self) -> None:
        if self._pump is not None:
            self._pump.close()
            self._pump = None
        if self._pipeline is not None:
            self._pipeline.stop()
        self._pipeline = None
        self._align_filter = None
