"""Isolated, persistent VTK trackball renderer; no camera/hardware access."""
import argparse
import json
from itertools import product
from pathlib import Path
import sys
from src.ui.plane_assets import point_rgb

EVENTS = {'LeftButtonPressEvent', 'LeftButtonReleaseEvent', 'MiddleButtonPressEvent',
          'MiddleButtonReleaseEvent', 'RightButtonPressEvent', 'RightButtonReleaseEvent',
          'MouseMoveEvent', 'MouseWheelForwardEvent', 'MouseWheelBackwardEvent'}


def reset_view(plotter):
    plotter.camera_position = 'iso'
    plotter.reset_camera()  # Center current bounds without changing any object points.
    plotter.render()
    bounds = plotter.bounds
    corners = []
    for x, y, z in product(bounds[:2], bounds[2:4], bounds[4:6]):
        plotter.renderer.SetWorldPoint(x, y, z, 1)
        plotter.renderer.WorldToDisplay()
        corners.append(plotter.renderer.GetDisplayPoint())
    width, height = plotter.window_size
    extent = max((max(p[0] for p in corners) - min(p[0] for p in corners)) / width,
                 (max(p[1] for p in corners) - min(p[1] for p in corners)) / height)
    if extent > 0:
        plotter.camera.zoom(.80 / extent)  # Roughly 80% fitted bounds, with margin.



def interact(plotter, command):
    event = command.get('event')
    if event == 'reset':
        reset_view(plotter)
    elif event in EVENTS:
        width, height = plotter.window_size
        interactor = plotter.iren.interactor
        interactor.SetEventInformation(int(command.get('x', .5) * (width - 1)),
                                      int((1 - command.get('y', .5)) * (height - 1)),
                                      int(command.get('control', False)), int(command.get('shift', False)))
        interactor.InvokeEvent(event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--interactive', action='store_true')
    args = parser.parse_args()
    import pyvista as pv
    cloud = pv.read(args.input)
    plotter = pv.Plotter(off_screen=True, window_size=(900, 600))
    try:
        colors = point_rgb(cloud)
        options = {'scalars': colors, 'rgb': True} if colors is not None else {'color': '#9eabb3'}
        plotter.add_mesh(cloud, **options, point_size=3, render_points_as_spheres=True,
                         show_scalar_bar=False)
        plotter.set_background('#090d12')
        plotter.enable_trackball_style()
        plotter.show(auto_close=False, interactive=False)
        plotter.iren.initialize()
        plotter.iren.interactor.Enable()
        reset_view(plotter)
        output = Path(args.output)
        temporary = output.with_name('next-' + output.name)
        def publish():
            plotter.screenshot(str(temporary))
            temporary.replace(output)
            if args.interactive:
                print(json.dumps({'frame': True, 'camera': list(plotter.camera_position)}), flush=True)
        publish()
        if args.interactive:
            for line in sys.stdin:
                interact(plotter, json.loads(line))
                publish()
    finally:
        plotter.close()


if __name__ == '__main__':
    main()
