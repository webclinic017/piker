"""
Qt UI styling.
"""
import pyqtgraph as pg
from PyQt5 import QtGui
from qdarkstyle.palette import DarkPalette


# chart-wide font
_font = QtGui.QFont("Hack")
# use pixel size to be cross-resolution compatible?
_font.setPixelSize(6)

# TODO: use QScreen to determine the same physical font size
# on screen despite different displays?
# PyQt docs: https://doc.qt.io/qtforpython/PySide2/QtGui/QScreen.html
#   - supposedly it's ``from QtGui import QScreen``
# Qt forums: https://forum.qt.io/topic/43625/point-sizes-are-they-reliable/4

_i3_rgba = QtGui.QColor.fromRgbF(*[0.14]*3 + [1])

# splitter widget config
_xaxis_at = 'bottom'

# charting config
CHART_MARGINS = (0, 0, 2, 2)
_min_points_to_show = 3
_bars_from_right_in_follow_mode = 5
_bars_to_left_in_follow_mode = 300


_tina_mode = False


def enable_tina_mode() -> None:
    """Enable "tina mode" to make everything look "conventional"
    like your pet hedgehog always wanted.
    """
    # white background (for tinas like our pal xb)
    pg.setConfigOption('background', 'w')


def hcolor(name: str) -> str:
    """Hex color codes by hipster speak.
    """
    return {
        # lives matter
        'black': '#000000',
        'erie_black': '#1B1B1B',
        'licorice': '#1A1110',
        'papas_special': '#06070c',
        'svags': '#0a0e14',

        # fifty shades
        'gray': '#808080',  # like the kick
        'jet': '#343434',
        'cadet': '#91A3B0',
        'marengo': '#91A3B0',
        'charcoal': '#36454F',
        'gunmetal': '#91A3B0',
        'battleship': '#848482',
        'davies': '#555555',
        'pikers': '#666666',  # like the cult

        # palette
        'default': DarkPalette.COLOR_BACKGROUND_NORMAL,
        'default_light': DarkPalette.COLOR_BACKGROUND_LIGHT,

        'white': '#ffffff',  # for tinas and sunbathers

        # blue zone
        'dad_blue': '#326693',  # like his shirt
        'vwap_blue': '#0582fb',
        'dodger_blue': '#1e90ff',  # like the team?
        'panasonic_blue': '#0040be',  # from japan

        # traditional
        'tina_green': '#00cc00',
        'tina_red': '#fa0000',

    }[name]
