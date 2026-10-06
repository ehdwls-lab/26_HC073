# Live inspection preview

```bash
python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply --execute
python -m src.tools.run_inspection_with_ui --profile blue --ui-3d-mode ply --execute
```

Without `--execute`, the launcher runs existing static production validation only:
no UI, camera, serial, projector, or motion. Execution retains the production
`EXECUTE` prompt and explicitly asks the operator to confirm the mechanical range.
GRAY selects `models/gray_v2/best_autoencoder.pth` and
`data/manifests/gray_v2/val.csv`; BLUE selects `models/blue_v1/best_autoencoder.pth`
and `data/manifests/blue_v1_rot180/val.csv`. Profiles only select model/manifest.
The launcher presets scan Z=0, safe Z=15, max Z=25, all-valid-planes, production adaptive
25→17 search, fixed by-id ports and cover OPEN=90/CLOSE=0/cleanup=CLOSE.
`--scan-z`, `--safe-z`, `--monitor`, and `--output-root` are explicit overrides;
existing production validation runs before creating either child process. Nonzero scan Z
is rejected in dry-run and execution; the constructor invariant is unchanged. Adaptive
start=25, min=17, coarse/fine steps=1, conveyor OUT=F10000, platform motion timeout=30s,
and surface coverage=1.0 come from production parsing/validation, independently of
scan/safe Z. Default monitor is HDMI-0.

Both profile dry runs print:

```text
structured-light scan/reference Z: 0.0
safe Z: 15.0
adaptive Z: start=25.0, max=25.0, min=17.0, coarse_step=1.0, fine_step=1.0
```

Architecture: launcher owns shared memory → separate spawned production and Qt
processes attach → launcher waits for production, then for the result window to
close → joins children and unlinks shared memory. Each execution gets a unique
`ui_session_*/run_*` directory, discovered by the UI without selecting old runs.
Only production constructs/starts OrbbecCameraController and opens hardware.
Closing/crashing the UI never signals production. Startup warmup publishes its existing
frames. With preview enabled, one optional FramePump then owns all aligned-pair reads;
production requests wait for fresh acquisitions and the same stream supplies preview. Publishing and stage metadata errors
are caught and logged as `[UI WARNING] preview unavailable`.

Transport: two fixed shared-memory slots, each containing a uint64 sequence,
UNIX timestamp, actual H/W and packed uint8 RGB. Capacity per slot is 1920×1080×3,
including the normal 1280×800×3 input. Production BGR is copied as RGB without
modifying its source. Each slot has a multiprocessing lock: both producer and reader
try locks without waiting, so a slow reader cannot stall acquisition and a reader
cannot see a partial copy. No frame queue exists. A separate fixed-size status
record contains stage, current inspection index and last observed R/P/Z, with NaN
for unknown coordinates. It updates on stage transitions, not as live telemetry.
Spawned children share the launcher's resource tracker; channel descriptors are
intended for these children, not independently launched interpreters.

Publishing is capped at 10fps; Qt checks newest frames every 100ms. The pump continues
acquiring while production processes or moves, within its existing camera-open interval.
Structured-light and pre-open gaps retain the last image with HOLD and stage text. Qt performs array/QImage conversion; a bounded background worker decodes
artifacts and adds green ROI and red threshold overlays only on matching saved final
RGB. Unregistered moving frames remain raw RGB. Patch grids
remain in Technical Detail. Results remain visible after production exits.

Default `--ui-3d-mode depth` never imports PyVistaQt. `--ui-3d-mode ply` explicitly
opts into a persistent, isolated interactive VTK renderer for one object per cycle;
Both profiles use current-run manifest `artifacts.integrated_object_only_ply`.
The two GRAY files are rendering references only. Explicit `--ui-object-ply` is a debug override.
it remains unverified on hardware. See [ownership audit](camera_ownership_preview.md). ImagePanel
initializes its pixmap before widget setup and tolerates resize before setup completes.

Software tests cover shared-memory shape/color/sequence, overwrite, busy/crashed-reader
slots, throttling, cross-process exit/cleanup, camera observer failures, stage failure
isolation, profile validation, artifact overlays, launcher supervision and offscreen Qt
resize/preview. No hardware execution is part of these tests.

Hardware: **NO-GO until actual verification** of camera refresh at production stages,
projector display, optical timing, UI crash isolation and motion on the real setup.
Abrupt OS kill/power loss is not a graceful cleanup path; production retains its existing
hardware cleanup behavior. SIGINT in the terminal is handled by production while the
launcher waits; the final result window can then be closed normally.
