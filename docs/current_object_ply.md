# Current-cycle object PLY audit

Production UI reads `artifacts.integrated_object_only_ply` from the current cycle's
completed `structured_light/structured_light_manifest.json`. Static GRAY references
and segmented/newest-PLY selection are removed from automatic production display.
Explicit `--ui-object-ply` remains a labelled manual/debug override only.

## Producer evidence

`서영 파트 파일/구조광_전처리_최종_v2_현재프레임플랫폼기준_Depth홀위상보강_경로수정_0822 (1).py`
creates these final integration products around lines 14238–14276:

- `01_v2_현재프레임기준_RAW_물체+플랫폼.ply`: raw relative object + platform.
- `02_v2_현재프레임기준_최종_물체+플랫폼.ply`: final integrated object + platform.
- `03_v2_현재프레임기준_최종_물체만.ply`: full object using `relative_final`,
  `object_solved`, and point colors. This is the canonical UI object.

Earlier/diagnostic exports in that producer are `structured_light_relative_pointcloud.ply`,
`01_플랫폼평면보정_RAW.ply`, and `02_플랫폼평면보정_기존Relative방식.ply`.
Downstream shell stages export `FINAL_DC_MASK_PHASE_z30_SIGN_PLUS.ply`, its
`_WITH_FLOOR.ply` counterpart, and `_dominant_plane_segmented.ply` (segmented plane
visualization). These are not selected as the UI full object.

The actual run `results/integrated_hardware/run_20260902_014320` contains all six
final integration/phase/floor/segmented files above, with canonical path in its manifest.
Manifest `ply_metadata` explicitly labels integrated object coordinates as
`image_centered_phase_relative`, z_sign=-1, z_scale=40, metric_z=false. Phase output
is likewise relative (z_sign=1, z_scale=30). Depth contributes to processing; this
does not make the exported visualization a claimed mm/cm reconstruction.

`ShellStructuredLightRunner._write_manifest()` already records the canonical artifact
only after the shell subprocess completes successfully. `IntegratedInspectionCycle`
archives run_info and copies this manifest through `_archive_structured_light()` after
pose postprocessing and before integrated camera inspection. These producer/control
paths are unchanged by this UI fix. No merge or `ui_object.ply` is needed or created.
Canonical display selection is not fed back into plane fitting, pose planning or AE.

## Observer and lifecycle

`current_object_ply()` resolves only this cycle's copied completion manifest.
Relative artifact paths are based on producer result_directory; absolute paths are
preserved. External original output requires the corresponding archived run_info.
The nested `ui_session_*/run_*` layout works without searching other sessions.
`IndustrialDashboard.reload()` reads the manifest even before cycle_result.json is
available. Presenter replay also uses the same resolver, replacing segmented heuristics.

Waiting → STRUCTURED LIGHT SCANNING / 3D MODEL GENERATING → LOADING 3D MODEL →
● 3D MODEL READY. No reference model appears while waiting. New cycles clear the old
model immediately. Pose changes only update metadata; the object and orbit/zoom/pan
viewpoint stay fixed. Initial bounds fit (~80%) runs on load or explicit RESET VIEW.

Interactive renderer errors remain UI-only and fall back to depth. Deep PLY decoding
happens in the isolated renderer; missing/non-PLY paths are rejected before launch.
Camera ownership and acquisition code are unchanged.

Hardware: **NO-GO until retest**. All checks for this change are software-only.
