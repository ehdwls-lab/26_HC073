"""Qt mouse surface for the isolated VTK trackball camera."""
from PySide6.QtCore import QEvent, Qt, Signal
from src.ui.widgets.image_panel import ImagePanel


class ObjectImagePanel(ImagePanel):
    interaction = Signal(dict)

    def __init__(self):
        super().__init__('')
        self.image.installEventFilter(self)
        self.image.setMouseTracking(True)
        self.image.setToolTip('Left drag: rotate · Middle/Shift+Left drag: pan · Wheel: zoom')

    def eventFilter(self, watched, event):
        kind = event.type()
        buttons = {Qt.LeftButton: 'Left', Qt.MiddleButton: 'Middle', Qt.RightButton: 'Right'}
        names = {QEvent.MouseButtonPress: 'ButtonPressEvent', QEvent.MouseButtonRelease: 'ButtonReleaseEvent'}
        if watched is not self.image or self._pixmap is None:
            return super().eventFilter(watched, event)
        if kind == QEvent.MouseButtonDblClick:
            self.interaction.emit({'event': 'reset'})
            return True
        if kind in names and event.button() in buttons:
            action = buttons[event.button()] + names[kind]
        elif kind == QEvent.MouseMove and event.buttons():
            action = 'MouseMoveEvent'
        elif kind == QEvent.Wheel:
            action = 'MouseWheelForwardEvent' if event.angleDelta().y() > 0 else 'MouseWheelBackwardEvent'
        else:
            return super().eventFilter(watched, event)
        fitted = self._pixmap.size().scaled(self.image.size(), Qt.KeepAspectRatio)
        x = (event.position().x() - (self.image.width() - fitted.width()) / 2) / max(1, fitted.width())
        y = (event.position().y() - (self.image.height() - fitted.height()) / 2) / max(1, fitted.height())
        self.interaction.emit(dict(event=action, x=max(0, min(1, x)), y=max(0, min(1, y)),
                                   shift=bool(event.modifiers() & Qt.ShiftModifier),
                                   control=bool(event.modifiers() & Qt.ControlModifier)))
        return True
