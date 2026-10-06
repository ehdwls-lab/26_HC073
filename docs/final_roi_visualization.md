# Final inspection ROI visualization

UI source is `inspection_planes[].anomaly_result.metadata.inspection_mask_path`, paired
with that metadata's `rgb_path`. The detector saves the exact inference `roi_mask` to
this path and reports its nonzero count in `inspection_area_px`. No surface, patch,
Auto-Z or candidate mask is substituted if this canonical metadata is missing.

Audit of `ui_session_36_qqvk7/run_20260906_202612`:

| Pose | Prior plane mask | Inference mask / reported inspection_area_px | Surface |
| --- | ---: | ---: | ---: |
| 1 (depth_rgb_seeded_fallback) | 46951 | 128872 / 128872 | 46322 |
| 2 (depth_external_contour_fill) | 161445 | 161445 / 161445 | 100354 |

Previously the presenter preferred the plane-level final geometry mask over the
anomaly metadata mask. Pose 1 therefore displayed a smaller pre-fallback artifact.
Pose 2 has no mask-count discrepancy; its display used contour only, without fill.
The anomaly localization panel previously used a heatmap-derived visualization rather
than the full inspection ROI. Neither code path cropped the RGB or selected a surface
mask directly; the audit does not establish additional hardware/display causes.

Both final main and localization views now use full RGB with a 20% green fill and
2px contour. Production's exact red threshold-overlay marks are restored last so red
remains visible over green. NORMAL retains the full green ROI. Patch artifacts remain
in Technical Detail. Full-frame shape mismatches are warned and never resized to fit.
ImagePanel continues aspect-preserving display scaling only after composition.

`[UI ROI]` logs path, full frame/mask shapes, nonzero count, coverage, bounding box,
final roi_type, and expected production pixel count. Count mismatch emits a warning.
Moving live frames remain unmodified; saved masks apply only to their final RGB.
No ROI generation, erosion, fallback, inference, threshold, classification, camera,
platform, conveyor or structured-light implementation is changed by this fix.

Hardware: NO-GO until UI verification.
