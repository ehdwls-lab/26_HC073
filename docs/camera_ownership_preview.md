# Camera ownership and continuous preview audit

The current runtime is CASE B: structured light opens its own camera in a child.
True full-cycle live RGB is therefore **not available** in this implementation.
No camera-open interval is extended across the structured-light boundary.

| Stage / entry point | Camera owner | UI |
| --- | --- | --- |
| Launcher / conveyor IN / initialization | Integrated camera remains closed | HOLD (no frame yet on a fresh run) |
| `ShellStructuredLightRunner.run_scan()` | `물체검사.sh` launches the canonical structured-light Python wrapper, which runs the preserved `(1).py` implementation; `카메라_열기()` creates its own SDK Pipeline | HOLD / 3D SCAN IN PROGRESS |
| Pose planning after child completion | No integrated stream yet | HOLD |
| Integrated camera start, startup warmup | Production process, one OrbbecCameraController Pipeline | LIVE from warmup frames |
| Pose transitions, Auto-Z, final geometry/ROI/RGB, analysis, judgement, conveyor OUT, cleanup before camera close | Same production process and pipeline; one acquisition thread after warmup | LIVE while aligned pairs arrive |
| Inspection completion | Production cleanup stops the pump, then its pipeline | Final result retained |
| UI / PLY renderer | Never opens camera or serial | Read-only consumers |

`IntegratedInspectionCycle.run()` waits synchronously for `run_scan()` before any
`self.camera.start()`. `ShellStructuredLightRunner._execute_script()` waits for child
completion with `communicate()` and terminates the child process group on timeout.
The preserved implementation owns pipeline start/stop. These paths are unchanged.

Independent tools are separate entry points, not children of the dashboard:
`capture_multiview_normal`, `live_roi_debug`, `test_orbbec_camera`,
`test_automatic_z_hardware` and `measure_depth_plane_angle` use OrbbecCameraController.
`capture_dataset`, `run_surface_inspection`, board/ROI/depth diagnostic scripts and
structured-light calibration/capture scripts instantiate SDK Pipeline directly.
They must not be launched concurrently with inspection. This change guarantees a
single owner within the integrated launcher lifecycle; it does not install a global
lock across manually launched external programs.

## Acquisition contract

Continuous preview is optional and enabled only when the production launcher supplies
a preview sink. Ordinary controller users retain synchronous capture. Startup settings
and N warmup acquisitions remain unchanged. After warmup, FramePump is the only caller
of the existing aligned-pair helper. Production capture waits for an acquisition that
started after its request: no cached or already in-flight frame is returned. Each
warmup/averaging consumer still requests its original number of fresh RGB/depth pairs.
The latest cache is bounded; SDK arrays are copied before sharing. Frame spacing may
change because of the new thread, so optical timing still needs hardware verification.
No motion, lighting, depth/ROI processing, model, or threshold decision is changed.

The existing publisher caps output at 10fps, uses nonblocking shared-memory slot locks,
and does not wait for UI consumption. Preview exceptions are warning-only. Acquisition
exceptions remain camera errors for production. Closing the controller cancels the
helper between SDK waits and joins the pump before stopping the pipeline. A stuck SDK
that does not respond within the shutdown bound is reported, never handed concurrently
to another acquisition/stop path. UI close does not call camera cleanup.

## Frame-safe display and PLY

Unregistered moving RGB carries no saved ROI or anomaly locations. Green ROI and red
production anomaly overlays are applied to the matching saved final analysis image.
HOLD retains the last image. This intentionally avoids using an old analysis image as
a replacement for every new live frame.

Both supplied PLYs are GRAY rendering references only. Existing copies are preserved;
no profile automatically selects them. `OBJECT_PLY_BY_PROFILE` has no production assets.
Explicit `--ui-object-ply` is a manual/debug override, labelled as such. Normal gray/blue
execution uses only the current cycle's completed canonical manifest; missing/invalid
canonical output falls back to depth, never a reference or another run.

`src/ui/current_object.py` reads exactly
`<cycle>/structured_light/structured_light_manifest.json`, field
`artifacts.integrated_object_only_ply`, requiring return_code=0 and finished_at.
Absolute paths are used directly. Relative artifact paths resolve against declared
result_directory (itself relative to the copied manifest directory if relative).
External producer directories require a matching archived run_info run_id and
result_directory; paths outside the current cycle without that link are rejected.
No PLY glob or newest-file search occurs in the UI. The UI polls this one manifest
before cycle_result.json exists, so loading can begin during subsequent inspection.
The first available object is latched for the cycle; pose updates preserve its view.
New-cycle detection immediately clears the old model and returns to scan waiting.

The panel title is `3D OBJECT / INSPECTION VIEW`. PART, POSE n/N, physical R/P/Z and
stage are metadata only. `STATIC OBJECT ASSET` distinguishes reference geometry from
current-run reconstruction. Physical platform angles never rotate the model or reset
the visualization camera. Original point RGB is retained (the supplied RGB itself
contains rainbow colors); no depth colormap, axes, grid or bounding box is added.

Default depth mode remains available. To select GRAY's alternate object:

```bash
.venv/bin/python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply \
  --ui-object-ply assets/ui/gray_object_02.ply --execute
```

PLY mode keeps a persistent, isolated PyVista/VTK renderer. Mouse coordinates from the
Qt panel are forwarded to VTK's trackball camera: left drag rotates, wheel zooms,
middle drag or Shift+left drag pans. RESET VIEW (and double click) restores the
isometric camera, centers bounds and fits with a margin around approximately 80% of
the panel. Files and point coordinates are never rewritten. Pose metadata updates
neither reload the renderer nor reset its viewpoint; new cycles and explicit reset do.

The interactive view is rendered offscreen in the child and presented in the main Qt
panel. This retains native-crash isolation instead of placing OpenGL in the LIVE UI.
Mouse moves coalesce, and only one render command is in flight. Startup/interaction
timeouts, load errors and crashes retain an available static image or fall back to
depth. The child is stopped on new cycle/window close, never on pose changes. Hardware
and FramePump ownership are unchanged.

Future full-cycle architecture: one CameraOwner/Frame Broker would provide UI frames,
structured-light snapshot requests, Auto-Z, ROI and final RGB from one SDK pipeline.
That requires separate structured-light timing validation and is not implemented here.

Hardware: **NO-GO until actual verification**, especially pump capture timing,
cleanup/cancellation, projector ownership handoff and live frame continuity.
