# piker: trading gear for hackers
# Copyright (C) Tyler Goodlet (in stewardship for piker0)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
UX interaction customs.
"""
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pprint import pformat
from typing import Optional, Dict, Callable, Any
import uuid

import trio
import pyqtgraph as pg
from pyqtgraph import ViewBox, Point, QtCore, QtGui
from pyqtgraph import functions as fn
import numpy as np

from ..log import get_logger
from ._style import _min_points_to_show, hcolor, _font
from ._graphics._lines import level_line, LevelLine
from .._ems import get_orders, OrderBook


log = get_logger(__name__)


class SelectRect(QtGui.QGraphicsRectItem):

    def __init__(
        self,
        viewbox: ViewBox,
        color: str = 'dad_blue',
    ) -> None:
        super().__init__(0, 0, 1, 1)

        # self.rbScaleBox = QtGui.QGraphicsRectItem(0, 0, 1, 1)
        self.vb = viewbox
        self._chart: 'ChartPlotWidget' = None  # noqa

        # override selection box color
        color = QtGui.QColor(hcolor(color))
        self.setPen(fn.mkPen(color, width=1))
        color.setAlpha(66)
        self.setBrush(fn.mkBrush(color))
        self.setZValue(1e9)
        self.hide()
        self._label = None

        label = self._label = QtGui.QLabel()
        label.setTextFormat(0)  # markdown
        label.setFont(_font.font)
        label.setMargin(0)
        label.setAlignment(
            QtCore.Qt.AlignLeft
            # | QtCore.Qt.AlignVCenter
        )

        # proxy is created after containing scene is initialized
        self._label_proxy = None
        self._abs_top_right = None

        # TODO: "swing %" might be handy here (data's max/min # % change)
        self._contents = [
            'change: {pchng:.2f} %',
            'range: {rng:.2f}',
            'bars: {nbars}',
            'max: {dmx}',
            'min: {dmn}',
            # 'time: {nbars}m',  # TODO: compute this per bar size
            'sigma: {std:.2f}',
        ]

    @property
    def chart(self) -> 'ChartPlotWidget':  # noqa
        return self._chart

    @chart.setter
    def chart(self, chart: 'ChartPlotWidget') -> None:  # noqa
        self._chart = chart
        chart.sigRangeChanged.connect(self.update_on_resize)
        palette = self._label.palette()

        # TODO: get bg color working
        palette.setColor(
            self._label.backgroundRole(),
            # QtGui.QColor(chart.backgroundBrush()),
            QtGui.QColor(hcolor('papas_special')),
        )

    def update_on_resize(self, vr, r):
        """Re-position measure label on view range change.

        """
        if self._abs_top_right:
            self._label_proxy.setPos(
                self.vb.mapFromView(self._abs_top_right)
            )

    def mouse_drag_released(
        self,
        p1: QtCore.QPointF,
        p2: QtCore.QPointF
    ) -> None:
        """Called on final button release for mouse drag with start and
        end positions.

        """
        self.set_pos(p1, p2)

    def set_pos(
        self,
        p1: QtCore.QPointF,
        p2: QtCore.QPointF
    ) -> None:
        """Set position of selection rectagle and accompanying label, move
        label to match.

        """
        if self._label_proxy is None:
            # https://doc.qt.io/qt-5/qgraphicsproxywidget.html
            self._label_proxy = self.vb.scene().addWidget(self._label)

        start_pos = self.vb.mapToView(p1)
        end_pos = self.vb.mapToView(p2)

        # map to view coords and update area
        r = QtCore.QRectF(start_pos, end_pos)

        # old way; don't need right?
        # lr = QtCore.QRectF(p1, p2)
        # r = self.vb.childGroup.mapRectFromParent(lr)

        self.setPos(r.topLeft())
        self.resetTransform()
        self.scale(r.width(), r.height())
        self.show()

        y1, y2 = start_pos.y(), end_pos.y()
        x1, x2 = start_pos.x(), end_pos.x()

        # TODO: heh, could probably use a max-min streamin algo here too
        _, xmn = min(y1, y2), min(x1, x2)
        ymx, xmx = max(y1, y2), max(x1, x2)

        pchng = (y2 - y1) / y1 * 100
        rng = abs(y1 - y2)

        ixmn, ixmx = round(xmn), round(xmx)
        nbars = ixmx - ixmn + 1

        data = self._chart._ohlc[ixmn:ixmx]

        if len(data):
            std = data['close'].std()
            dmx = data['high'].max()
            dmn = data['low'].min()
        else:
            dmn = dmx = std = np.nan

        # update label info
        self._label.setText('\n'.join(self._contents).format(
            pchng=pchng, rng=rng, nbars=nbars,
            std=std, dmx=dmx, dmn=dmn,
        ))

        # print(f'x2, y2: {(x2, y2)}')
        # print(f'xmn, ymn: {(xmn, ymx)}')

        label_anchor = Point(xmx + 2, ymx)

        # XXX: in the drag bottom-right -> top-left case we don't
        # want the label to overlay the box.
        # if (x2, y2) == (xmn, ymx):
        #     # could do this too but needs to be added after coords transform
        #     # label_anchor = Point(x2, y2 + self._label.height())
        #     label_anchor = Point(xmn, ymn)

        self._abs_top_right = label_anchor
        self._label_proxy.setPos(self.vb.mapFromView(label_anchor))
        self._label.show()

    def clear(self):
        """Clear the selection box from view.

        """
        self._label.hide()
        self.hide()


# global store of order-lines graphics
# keyed by uuid4 strs - used to sync draw
# order lines **after** the order is 100%
# active in emsd
_order_lines: Dict[str, LevelLine] = {}


@dataclass
class LineEditor:
    view: 'ChartView'
    _order_lines: field(default_factory=_order_lines)
    chart: 'ChartPlotWidget' = None  # type: ignore # noqa
    _active_staged_line: LevelLine = None
    _stage_line: LevelLine = None

    def stage_line(self, color: str = 'alert_yellow') -> LevelLine:
        """Stage a line at the current chart's cursor position
        and return it.

        """
        chart = self.chart._cursor.active_plot
        chart.setCursor(QtCore.Qt.PointingHandCursor)
        cursor = chart._cursor
        y = chart._cursor._datum_xy[1]

        line = self._stage_line
        if not line:
            # add a "staged" cursor-tracking line to view
            # and cash it in a a var
            line = level_line(
                chart,
                level=y,
                digits=chart._lc.symbol.digits(),
                color=color,

                # don't highlight the "staging" line
                hl_on_hover=False,
            )
            self._stage_line = line

        else:
            # use the existing staged line instead
            # of allocating more mem / objects repeatedly
            line.setValue(y)
            line.show()
            line.color = color
            line.label.color = color
            line.label.show()

        self._active_staged_line = line

        # hide crosshair y-line
        cursor.graphics[chart]['hl'].hide()

        # add line to cursor trackers
        cursor._trackers.add(line)

        return line

    def unstage_line(self) -> LevelLine:
        """Inverse of ``.stage_line()``.

        """
        chart = self.chart._cursor.active_plot
        chart.setCursor(QtCore.Qt.ArrowCursor)
        cursor = chart._cursor

        # delete "staged" cursor tracking line from view
        line = self._active_staged_line

        cursor._trackers.remove(line)

        if self._stage_line:
            self._stage_line.hide()
            self._stage_line.label.hide()

        self._active_staged_line = None

        # show the crosshair y line
        hl = cursor.graphics[chart]['hl']
        hl.show()

    def create_line(
        self,
        uuid: str
    ) -> LevelLine:

        line = self._active_staged_line
        if not line:
            raise RuntimeError("No line commit is currently staged!?")

        chart = self.chart._cursor.active_plot
        y = chart._cursor._datum_xy[1]

        line = level_line(
            chart,
            level=y,
            color=line.color,
            digits=chart._lc.symbol.digits(),
            show_label=False,
        )

        # register for later lookup/deletion
        self._order_lines[uuid] = line
        return line, y

    def commit_line(self, uuid: str) -> LevelLine:
        """Commit a "staged line" to view.

        Submits the line graphic under the cursor as a (new) permanent
        graphic in view.

        """
        line = self._order_lines[uuid]
        line.oid = uuid
        line.label.show()

        # TODO: other flashy things to indicate the order is active

        log.debug(f'Level active for level: {line.value()}')

        return line

    def lines_under_cursor(self):
        """Get the line(s) under the cursor position.

        """
        # Delete any hoverable under the cursor
        return self.chart._cursor._hovered

    def remove_line(
        self,
        line: LevelLine = None,
        uuid: str = None,
    ) -> LevelLine:
        """Remove a line by refernce or uuid.

        If no lines or ids are provided remove all lines under the
        cursor position.

        """
        if line:
            uuid = line.oid

        # try to look up line from our registry
        line = self._order_lines.pop(uuid)

        # if hovered remove from cursor set
        hovered = self.chart._cursor._hovered
        if line in hovered:
            hovered.remove(line)

        line.delete()
        return line


@dataclass
class ArrowEditor:

    chart: 'ChartPlotWidget'  # noqa
    _arrows: field(default_factory=dict)

    def add(
        self,
        uid: str,
        x: float,
        y: float,
        color='default',
        pointing: str = 'up',
    ) -> pg.ArrowItem:
        """Add an arrow graphic to view at given (x, y).

        """
        angle = 90 if pointing == 'up' else -90

        yb = pg.mkBrush(hcolor(color))
        arrow = pg.ArrowItem(
            angle=angle,
            baseAngle=0,
            headLen=5,
            headWidth=2,
            tailLen=None,

            # coloring
            pen=pg.mkPen(hcolor('papas_special')),
            brush=yb,
        )
        arrow.setPos(x, y)

        self._arrows[uid] = arrow

        # render to view
        self.chart.plotItem.addItem(arrow)

        return arrow

    def remove(self, arrow) -> bool:
        self.chart.plotItem.removeItem(arrow)


@dataclass
class OrderMode:
    """Major mode for placing orders on a chart view.

    """
    chart: 'ChartPlotWidget'  #  type: ignore # noqa
    book: OrderBook
    lines: LineEditor
    arrows: ArrowEditor
    _colors = {
        'alert': 'alert_yellow',
        'buy': 'buy_green',
        'sell': 'sell_red',
    }
    _action: str = 'alert'

    key_map: Dict[str, Callable] = field(default_factory=dict)

    def uuid(self) -> str:
        return str(uuid.uuid4())

    def set_exec(self, name: str) -> None:
        """Set execution mode.

        """
        self._action = name
        self.lines.stage_line(color=self._colors[name])

    def on_submit(self, uuid: str) -> dict:
        self.lines.commit_line(uuid)
        req_msg = self.book._sent_orders.get(uuid)
        req_msg['ack_time_ns'] = time.time_ns()
        # self.book._confirmed_orders[uuid] = req_msg
        return req_msg

    def on_fill(
        self,
        uuid: str,
        msg: Dict[str, Any],
    ) -> None:
        log.info(f'New fill\n{pformat(msg)}')
        line = self.lines._order_lines[uuid]

        # XXX: not sure why the time is so off here
        # looks like we're gonna have to do some fixing..
        ohlc = self.chart._shm.array
        indexes = ohlc['time'] >= msg['broker_time']
        if any(indexes):
            arrow_index = ohlc['index'][indexes[-1]]
        else:
            arrow_index = ohlc['index'][-1]

        self.arrows.add(
            uuid,
            arrow_index,
            msg['price'],
            pointing='up' if msg['action'] == 'buy' else 'down',
            color=line.color
        )

    async def on_exec(
        self,
        uuid: str,
        msg: Dict[str, Any],
    ) -> None:

        # only once all fills have cleared and the execution
        # is complet do we remove our "order line"
        line = self.lines.remove_line(uuid=uuid)
        log.debug(f'deleting {line} with oid: {uuid}')

        # DESKTOP NOTIFICATIONS
        #
        # TODO: this in another task?
        # not sure if this will ever be a bottleneck,
        # we probably could do graphics stuff first tho?

        # XXX: linux only for now
        result = await trio.run_process(
            [
                'notify-send',
                '-u', 'normal',
                '-t', '10000',
                'piker',
                f'alert: {msg}',
            ],
        )
        log.runtime(result)

    def on_cancel(self, uuid: str) -> None:
        msg = self.book._sent_orders.pop(uuid, None)
        if msg is not None:
            self.lines.remove_line(uuid=uuid)
        else:
            log.warning(
                f'Received cancel for unsubmitted order {pformat(msg)}'
            )

    def submit_exec(self) -> None:
        """Send execution order to EMS.

        """
        # register the "staged" line under the cursor
        # to be displayed when above order ack arrives
        # (means the line graphic doesn't show on screen until the
        # order is live in the emsd).
        uid = str(uuid.uuid4())

        # make line graphic
        line, y = self.lines.create_line(uid)
        line.oid = uid

        # send order cmd to ems
        self.book.send(
            uuid=uid,
            symbol=self.chart._lc._symbol,
            price=y,
            action=self._action,
        )


@asynccontextmanager
async def open_order_mode(
    chart,
):
    # global _order_lines

    view = chart._vb
    book = get_orders()
    lines = LineEditor(view=view, _order_lines=_order_lines, chart=chart)
    arrows = ArrowEditor(chart, {})

    log.info("Opening order mode")

    mode = OrderMode(chart, book, lines, arrows)
    view.mode = mode

    # # setup local ui event streaming channels for request/resp
    # # streamging with EMS daemon
    # global _to_ems, _from_order_book
    # _to_ems, _from_order_book = trio.open_memory_channel(100)

    try:
        yield mode

    finally:
        # XXX special teardown handling like for ex.
        # - cancelling orders if needed?
        # - closing positions if desired?
        # - switching special condition orders to safer/more reliable variants
        log.info("Closing order mode")


class ChartView(ViewBox):
    """Price chart view box with interaction behaviors you'd expect from
    any interactive platform:

        - zoom on mouse scroll that auto fits y-axis
        - vertical scrolling on y-axis
        - zoom on x to most recent in view datum
        - zoom on right-click-n-drag to cursor position

    """
    def __init__(
        self,
        parent: pg.PlotItem = None,
        **kwargs,
    ):
        super().__init__(parent=parent, **kwargs)
        # disable vertical scrolling
        self.setMouseEnabled(x=True, y=False)
        self.linked_charts = None
        self.select_box = SelectRect(self)
        self.addItem(self.select_box, ignoreBounds=True)
        self._chart: 'ChartPlotWidget' = None  # noqa

        # self._lines_editor = LineEditor(view=self, _lines=_lines)
        self.mode = None

        # kb ctrls processing
        self._key_buffer = []

    @property
    def chart(self) -> 'ChartPlotWidget':  # type: ignore # noqa
        return self._chart

    @chart.setter
    def chart(self, chart: 'ChartPlotWidget') -> None:  # type: ignore # noqa
        self._chart = chart
        self.select_box.chart = chart
        # self._lines_editor.chart = chart

    def wheelEvent(self, ev, axis=None):
        """Override "center-point" location for scrolling.

        This is an override of the ``ViewBox`` method simply changing
        the center of the zoom to be the y-axis.

        TODO: PR a method into ``pyqtgraph`` to make this configurable
        """

        if axis in (0, 1):
            mask = [False, False]
            mask[axis] = self.state['mouseEnabled'][axis]
        else:
            mask = self.state['mouseEnabled'][:]

        # don't zoom more then the min points setting
        l, lbar, rbar, r = self.linked_charts.chart.bars_range()
        vl = r - l

        if ev.delta() > 0 and vl <= _min_points_to_show:
            log.debug("Max zoom bruh...")
            return

        if ev.delta() < 0 and vl >= len(self.linked_charts.chart._ohlc) + 666:
            log.debug("Min zoom bruh...")
            return

        # actual scaling factor
        s = 1.015 ** (ev.delta() * -1 / 20)  # self.state['wheelScaleFactor'])
        s = [(None if m is False else s) for m in mask]

        # center = pg.Point(
        #     fn.invertQTransform(self.childGroup.transform()).map(ev.pos())
        # )

        # XXX: scroll "around" the right most element in the view
        # which stays "pinned" in place.

        # furthest_right_coord = self.boundingRect().topRight()

        # yaxis = pg.Point(
        #     fn.invertQTransform(
        #         self.childGroup.transform()
        #     ).map(furthest_right_coord)
        # )

        # This seems like the most "intuitive option, a hybrid of
        # tws and tv styles
        last_bar = pg.Point(int(rbar))

        self._resetTarget()
        self.scaleBy(s, last_bar)
        ev.accept()
        self.sigRangeChangedManually.emit(mask)

    def mouseDragEvent(
        self,
        ev,
        axis: Optional[int] = None,
    ) -> None:
        #  if axis is specified, event will only affect that axis.
        ev.accept()  # we accept all buttons
        button = ev.button()

        pos = ev.pos()
        lastPos = ev.lastPos()
        dif = pos - lastPos
        dif = dif * -1

        # Ignore axes if mouse is disabled
        mouseEnabled = np.array(self.state['mouseEnabled'], dtype=np.float)
        mask = mouseEnabled.copy()
        if axis is not None:
            mask[1-axis] = 0.0

        # Scale or translate based on mouse button
        if button & (QtCore.Qt.LeftButton | QtCore.Qt.MidButton):

            # zoom y-axis ONLY when click-n-drag on it
            if axis == 1:
                # set a static y range special value on chart widget to
                # prevent sizing to data in view.
                self.chart._static_yrange = 'axis'

                scale_y = 1.3 ** (dif.y() * -1 / 20)
                self.setLimits(yMin=None, yMax=None)

                # print(scale_y)
                self.scaleBy((0, scale_y))

            if self.state['mouseMode'] == ViewBox.RectMode:

                down_pos = ev.buttonDownPos()

                # This is the final position in the drag
                if ev.isFinish():

                    self.select_box.mouse_drag_released(down_pos, pos)

                    # ax = QtCore.QRectF(down_pos, pos)
                    # ax = self.childGroup.mapRectFromParent(ax)
                    # print(ax)

                    # this is the zoom transform cmd
                    # self.showAxRect(ax)

                    # self.axHistoryPointer += 1
                    # self.axHistory = self.axHistory[
                    #     :self.axHistoryPointer] + [ax]
                else:
                    self.select_box.set_pos(down_pos, pos)

                    # update shape of scale box
                    # self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            else:
                # default bevavior: click to pan view

                tr = self.childGroup.transform()
                tr = fn.invertQTransform(tr)
                tr = tr.map(dif*mask) - tr.map(Point(0, 0))

                x = tr.x() if mask[0] == 1 else None
                y = tr.y() if mask[1] == 1 else None

                self._resetTarget()

                if x is not None or y is not None:
                    self.translateBy(x=x, y=y)

                self.sigRangeChangedManually.emit(self.state['mouseEnabled'])

        elif button & QtCore.Qt.RightButton:

            # right click zoom to center behaviour

            if self.state['aspectLocked'] is not False:
                mask[0] = 0

            dif = ev.screenPos() - ev.lastScreenPos()
            dif = np.array([dif.x(), dif.y()])
            dif[0] *= -1
            s = ((mask * 0.02) + 1) ** dif

            tr = self.childGroup.transform()
            tr = fn.invertQTransform(tr)

            x = s[0] if mouseEnabled[0] == 1 else None
            y = s[1] if mouseEnabled[1] == 1 else None

            center = Point(tr.map(ev.buttonDownPos(QtCore.Qt.RightButton)))
            self._resetTarget()
            self.scaleBy(x=x, y=y, center=center)
            self.sigRangeChangedManually.emit(self.state['mouseEnabled'])

    def mouseClickEvent(self, ev):
        """Full-click callback.

        """
        button = ev.button()
        # pos = ev.pos()

        if button == QtCore.Qt.RightButton and self.menuEnabled():
            ev.accept()
            self.raiseContextMenu(ev)

        elif button == QtCore.Qt.LeftButton:
            # when in order mode, submit execution
            ev.accept()
            self.mode.submit_exec()

    def keyReleaseEvent(self, ev):
        """
        Key release to normally to trigger release of input mode

        """
        # TODO: is there a global setting for this?
        if ev.isAutoRepeat():
            ev.ignore()
            return

        ev.accept()
        text = ev.text()
        key = ev.key()
        # mods = ev.modifiers()

        if key == QtCore.Qt.Key_Shift:
            # if self.state['mouseMode'] == ViewBox.RectMode:
            self.setMouseMode(ViewBox.PanMode)

        if text in {'a', 'f', 's'}:
            # draw "staged" line under cursor position
            self.mode.lines.unstage_line()

    def keyPressEvent(self, ev):
        """
        This routine should capture key presses in the current view box.

        """
        # TODO: is there a global setting for this?
        if ev.isAutoRepeat():
            ev.ignore()
            return

        ev.accept()
        text = ev.text()
        key = ev.key()
        mods = ev.modifiers()

        if mods == QtCore.Qt.ShiftModifier:
            if self.state['mouseMode'] == ViewBox.PanMode:
                self.setMouseMode(ViewBox.RectMode)

        # ctl
        if mods == QtCore.Qt.ControlModifier:
            # TODO: ctrl-c as cancel?
            # https://forum.qt.io/topic/532/how-to-catch-ctrl-c-on-a-widget/9
            # if ev.text() == 'c':
            #     self.rbScaleBox.hide()
            print(f"CTRL + key:{key} + text:{text}")

        # alt
        if mods == QtCore.Qt.AltModifier:
            pass

        # esc
        if key == QtCore.Qt.Key_Escape:
            self.select_box.clear()

        self._key_buffer.append(text)

        # View modes
        if text == 'r':
            self.chart.default_view()

        # Order modes
        # stage orders at the current cursor level
        elif text == 's':
            self.mode.set_exec('sell')

        elif text == 'f':
            self.mode.set_exec('buy')

        elif text == 'a':
            self.mode.set_exec('alert')

        # delete orders under cursor
        elif text == 'd':

            # delete any lines under the cursor
            mode = self.mode
            for line in mode.lines.lines_under_cursor():
                mode.book.cancel(uuid=line.oid)

        # XXX: Leaving this for light reference purposes, there
        # seems to be some work to at least gawk at for history mgmt.

        # Key presses are used only when mouse mode is RectMode
        # The following events are implemented:
        # ctrl-A : zooms out to the default "full" view of the plot
        # ctrl-+ : moves forward in the zooming stack (if it exists)
        # ctrl-- : moves backward in the zooming stack (if it exists)

        #     self.scaleHistory(-1)
        # elif ev.text() in ['+', '=']:
        #     self.scaleHistory(1)
        # elif ev.key() == QtCore.Qt.Key_Backspace:
        #     self.scaleHistory(len(self.axHistory))
        else:
            ev.ignore()
