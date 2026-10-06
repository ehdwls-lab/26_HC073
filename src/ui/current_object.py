"""Read only the current cycle's completed producer manifest, never search PLYs."""
import json
from pathlib import Path


def current_object_ply(run_dir):
    root = Path(run_dir).expanduser().resolve()
    manifest_path = root / 'structured_light/structured_light_manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('return_code') != 0 or not manifest.get('finished_at'):
            return None
        value = manifest.get('artifacts', {}).get('integrated_object_only_ply')
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            # Producer-relative artifacts are relative to its declared result_directory.
            source = Path(manifest.get('result_directory') or '.')
            if not source.is_absolute():
                source = manifest_path.parent / source
            path = source / path
        path = path.resolve()
        if not path.is_relative_to(root):
            # External producer output is permitted only through this cycle's archived run_info.
            info = json.loads((manifest_path.parent / 'run_info.json').read_text(encoding='utf-8'))
            source = Path(info['result_directory']).resolve()
            if manifest.get('run_id') != info.get('run_id') or not path.is_relative_to(source):
                return None
            if Path(manifest['result_directory']).resolve() != source:
                return None
        if path.suffix.lower() != '.ply' or not path.is_file():
            return None
        with path.open('rb') as handle:
            if handle.readline().strip() != b'ply':
                return None
        return path
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
