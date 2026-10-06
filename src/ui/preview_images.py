"""Artifact decoding/composition for the dashboard's background worker."""
import logging
import cv2
import numpy as np

_LOGGED = set()

def final_roi_overlay(rgb, pose, show_anomaly=True):
    if rgb is None:
        return None
    output = rgb.copy()
    mask = cv2.imread(str(pose.mask), cv2.IMREAD_GRAYSCALE) if pose.mask else None
    if mask is None or mask.shape != rgb.shape[:2]:
        logging.warning("[UI ROI] final_inspection_mask unavailable or shape mismatch: %s", pose.mask)
        return output
    inside = mask > 0
    count = int(np.count_nonzero(inside))
    ys, xs = np.nonzero(inside)
    bbox = None if not count else (int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1))
    expected = getattr(pose, "inspection_area_px", None)
    key = (str(pose.mask), count, expected)
    if key not in _LOGGED:
        logging.info("[UI ROI] source=final_inspection_mask path=%s frame_shape=%s mask_shape=%s inspection_px=%s coverage=%.2f%% bbox=%s roi_type=%s production_inspection_px=%s",
                     pose.mask, rgb.shape[:2], mask.shape, count, 100*count/mask.size, bbox, getattr(pose, "roi_type", None), expected)
        if expected is not None and count != expected:
            logging.warning("[UI ROI] pixel count mismatch: production=%s UI=%s", expected, count)
        if len(_LOGGED) >= 128:
            _LOGGED.clear()
        _LOGGED.add(key)
    output[inside] = np.round(rgb[inside] * .8 + np.array([0,255,0]) * .2).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(output, contours, -1, (0,255,0), 2)
    if show_anomaly and pose.overlay:
        saved = load_bgr(pose.overlay)
        if saved is not None and saved.shape == rgb.shape:
            # Production make_threshold_overlay writes exact red contours on final RGB.
            red = np.all(saved == (0,0,255), axis=2) & np.any(saved != rgb, axis=2)
            output[red] = (0,0,255)
    return output

from src.ui.image_utils import (anomaly_localization_overlay, depth_preview, load_bgr,
                                roi_contour_overlay)


def prepare_pose_images(pose, reveal):
    rgb = load_bgr(pose.rgb)
    depth = depth_preview(pose.depth)
    heat = load_bgr(pose.heatmap)
    localization = final_roi_overlay(rgb, pose, show_anomaly=True)
    live = final_roi_overlay(rgb, pose, show_anomaly=reveal >= 4) if reveal >= 3 else rgb if reveal >= 2 else None
    details = (rgb, depth, load_bgr(pose.mask), load_bgr(pose.patch_overlay), heat,
               load_bgr(pose.overlay), load_bgr(pose.board_overlay), depth)
    return live, localization if reveal >= 4 else None, depth, details


def prepare_live_overlay(run_dir, pose_index, bgr):
    """Use only the current pose's artifacts; never borrow a previous pose's ROI."""
    # No frame registration exists for live frames. Saved ROI and anomaly are
    # applied only by prepare_pose_images to the matching final analysis RGB.
    return bgr
