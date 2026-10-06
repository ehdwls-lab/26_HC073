"""Single command, separate production/UI processes; dry-run unless --execute."""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
from multiprocessing.reduction import DupFd
import os
from pathlib import Path
import signal
import sys
import tempfile

from src.ui.live_preview import PreviewChannel, SharedMemoryPreviewPublisher

ROOT = Path(__file__).resolve().parents[2]
PORTS = (
    '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0',
    '/dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_066FFF383133524157152339-if02',
    '/dev/serial/by-id/usb-Arduino__www.arduino.cc__0043_75932313039351C09122-if00',
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('gray', 'blue'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--ui-object-ply', type=Path)
    parser.add_argument('--ui-3d-mode', choices=('depth', 'ply'), default='depth')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'results/integrated_hardware')
    parser.add_argument('--monitor', default='HDMI-0')
    parser.add_argument('--scan-z', type=float, default=0.0)
    parser.add_argument('--safe-z', type=float, default=15.0)
    return parser


def production_arguments(args, output_root):
    # Scan/reference (0), cleanup safe Z (15), and adaptive start (25) are independent.
    # Adaptive start/steps/min, conveyor OUT, motion, and coverage use production defaults.
    model_profile, manifest_profile = {
        'gray': ('gray_v2', 'gray_v2'),
        'blue': ('blue_v1', 'blue_v1_rot180'),
    }[args.profile]
    command = [
        '--conveyor-port', PORTS[0], '--platform-port', PORTS[1], '--lighting-port', PORTS[2],
        '--cover-open-angle', '90', '--cover-close-angle', '0',
        '--conveyor-steps', '6325', '--monitor', args.monitor,
        '--scan-z', str(args.scan_z), '--safe-z', str(args.safe_z), '--z-max', '25',
        '--pose-plan-mode', 'all_valid_planes',
        '--quality-config', str(ROOT / 'config/automatic_z_quality.json'),
        '--output-root', str(output_root),
        '--anomaly-model', str(ROOT / 'models' / model_profile / 'best_autoencoder.pth'),
        '--anomaly-val-manifest', str(ROOT / 'data/manifests' / manifest_profile / 'val.csv'),
    ]
    if args.execute:
        # The production child's EXECUTE prompt explicitly confirms this acknowledgement.
        command += ['--execute', '--ack-mechanical-range']
    return command


def production_main(arguments, channel, stdin_fd=None):
    if stdin_fd is not None:
        sys.stdin = os.fdopen(stdin_fd.detach(), 'r')
    from src.tools.test_integrated_inspection_cycle import build_parser as production_parser, run
    publisher = None
    try:
        if channel is not None:
            try:
                publisher = SharedMemoryPreviewPublisher(channel)
            except Exception:
                logging.warning('[UI WARNING] preview unavailable')
        def confirm(prompt):
            return input('Confirm the configured mechanical range is safe for this setup.\n' + prompt)
        code = run(production_parser().parse_args(arguments), confirmation_input=confirm,
                   preview_sink=publisher)
    finally:
        if publisher is not None:
            try:
                publisher.close()
            except Exception:
                logging.warning('[UI WARNING] preview unavailable')
    raise SystemExit(code)


def ui_main(run_root, mode, channel, profile=None, object_ply=None):
    # Ctrl-C belongs to production, where the existing hardware cleanup handles it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from src.tools.run_inspection_ui import main
    arguments = ['--mode', 'live', '--run', str(run_root), '--watch-root', '--ui-3d-mode', mode]
    if profile:
        arguments += ['--profile', profile]
    if object_ply:
        arguments += ['--ui-object-ply', str(object_ply)]
    raise SystemExit(main(arguments, preview_channel=channel))


def supervise(production, ui):
    """UI exit never sends a signal to production. Retain UI after production exits."""
    production.join()
    code = production.exitcode
    if ui is not None:
        ui.join()
        if ui.exitcode:
            logging.warning('[UI WARNING] preview unavailable (UI exited with %s)', ui.exitcode)
    return code


def main(argv=None):
    args = build_parser().parse_args(argv)
    from src.tools.test_integrated_inspection_cycle import (
        build_parser as production_parser, _validate_static, run,
    )
    production_args = production_parser().parse_args(production_arguments(args, args.output_root))
    # Reuse production preflight before creating the session, IPC, or either child.
    _validate_static(production_args)
    if not args.execute:
        return run(production_args)
    args.output_root.mkdir(parents=True, exist_ok=True)
    # Unique parent prevents the observer from ever selecting a previous inspection.
    session = Path(tempfile.mkdtemp(prefix='ui_session_', dir=args.output_root))
    context = mp.get_context('spawn')
    channel = None
    ui = None
    production = None
    try:
        try:
            channel = PreviewChannel.create(context)
        except Exception:
            logging.warning('[UI WARNING] preview unavailable')
        try:
            ui = context.Process(target=ui_main, args=(session, args.ui_3d_mode, channel, args.profile, args.ui_object_ply), name='inspection-ui')
            ui.start()
        except Exception:
            logging.warning('[UI WARNING] preview unavailable')
            ui = None
        production = context.Process(target=production_main,
            args=(production_arguments(args, session), channel, DupFd(sys.stdin.fileno())),
            name='inspection-production')
        production.start()
        # Terminal SIGINT is delivered to production too; only it owns hardware cleanup.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return supervise(production, ui)
        finally:
            signal.signal(signal.SIGINT, previous)
    finally:
        # No termination of a running inspection, including on launcher-side failures.
        if production is not None and production.pid is not None:
            production.join()
        if ui is not None and ui.pid is not None:
            if ui.is_alive():
                ui.terminate()
            ui.join()
        if channel is not None:
            try:
                channel.unlink()
            except Exception:
                logging.warning('[UI WARNING] preview unavailable (shared-memory cleanup failed)')


if __name__ == '__main__':
    raise SystemExit(main())
