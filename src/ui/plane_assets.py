"""Static visualization references, never current-run metric reconstructions."""
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / 'assets/ui'


GRAY_OBJECT_PLY_DEFAULT = ASSET_ROOT / 'gray_object_01.ply'
GRAY_OBJECT_PLY_ALTERNATE = ASSET_ROOT / 'gray_object_02.ply'
# These constants are reference/test assets, never automatic production sources.
OBJECT_PLY_BY_PROFILE = {'gray': None, 'blue': None}


class CycleObjectAsset:
    """Latch one existing object per cycle; pose indices never select assets."""
    def __init__(self):
        self.cycle = None
        self.selected = None
        self.reference = False
        self.preferred = None

    def select(self, cycle, profile=None, override=None, current_run=None):
        if cycle != self.cycle:
            self.cycle = cycle
            self.selected = None
            self.reference = False
            self.preferred = Path(override).expanduser().resolve() if override else None
        if self.selected is None:
            for candidate, reference in ((self.preferred, True), (current_run, False)):
                if candidate is not None and Path(candidate).is_file():
                    self.selected = Path(candidate).resolve()
                    self.reference = reference
                    break
        return self.selected


def point_rgb(cloud):
    for name in ('RGB', 'rgb', 'RGBA', 'rgba'):
        if name in cloud.point_data:
            return cloud.point_data[name][:, :3]
    if all(name in cloud.point_data for name in ('red', 'green', 'blue')):
        import numpy as np
        return np.column_stack([cloud.point_data[name] for name in ('red', 'green', 'blue')])
    return None
