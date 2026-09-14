from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .waveform import WaveformPoint, normalize_waveform_points, parse_waveform_points, preset_waveform, serialize_waveform_points


STYLE = """
QWidget { background: #101720; color: #e5edf5; font: 9pt 'Segoe UI'; }
QMainWindow, QSplitter { background: #090d12; }
QFrame#panel { background: #151e29; border: 1px solid #2a3949; }
QLabel#section { color: #38bdf8; font-weight: 600; padding-top: 6px; }
QPushButton, QComboBox, QDoubleSpinBox { background: #0b1118; border: 1px solid #33475b; padding: 6px; }
QPushButton:hover { border-color: #38bdf8; background: #172536; }
QPushButton#apply { background: #0e7490; border-color: #38bdf8; font-weight: 600; }
QTableWidget { background: #080d13; alternate-background-color: #101a25; border: 1px solid #2a3949; gridline-color: #203040; }
QHeaderView::section { background: #1c2835; color: #e5edf5; padding: 6px; border: 0; border-right: 1px solid #2a3949; }
QToolBar { background: #101720; border-bottom: 1px solid #2a3949; spacing: 4px; }
QToolButton { background: #1c2835; color: #e5edf5; padding: 6px 10px; border: 1px solid #2a3949; }
QToolButton:hover { border-color: #38bdf8; }
QStatusBar { background: #101720; color: #8da0b3; }
"""


class EditorViewBox(pg.ViewBox):
    def __init__(self, editor: "WaveformWindow") -> None:
        super().__init__(enableMenu=False)
        self.editor = editor
        self.setMouseMode(self.PanMode)

    def mouseClickEvent(self, event) -> None:  # noqa: N802
        point = self.mapSceneToView(event.scenePos())
        modifiers = QApplication.keyboardModifiers()
        if event.button() == Qt.MouseButton.LeftButton:
            is_double = bool(getattr(event, "double", lambda: False)())
            if is_double or modifiers & Qt.KeyboardModifier.ControlModifier:
                self.editor.add_point(float(point.x()), float(point.y()))
                event.accept()
                return
        super().mouseClickEvent(event)

class DraggablePoints(pg.ScatterPlotItem):
    drag_started = Signal(int)
    point_dragged = Signal(int, float, float)
    drag_finished = Signal()
    point_selected = Signal(int)
    point_deleted = Signal(int)

    def __init__(self) -> None:
        super().__init__(size=10, pen=pg.mkPen("#fb7185", width=1.5), brush=pg.mkBrush("#101720"), hoverable=True, hoverSize=14)
        self.drag_index: int | None = None

    def mouseDragEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if event.isStart():
            spots = self.pointsAt(event.buttonDownPos())
            self.drag_index = int(spots[0].data()) if len(spots) else None
            if self.drag_index is not None:
                self.drag_started.emit(self.drag_index)
        if self.drag_index is not None:
            position = event.pos()
            self.point_dragged.emit(self.drag_index, float(position.x()), float(position.y()))
            event.accept()
        else:
            event.ignore()
        if event.isFinish():
            if self.drag_index is not None:
                self.drag_finished.emit()
            self.drag_index = None

    def mouseClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            spots = self.pointsAt(event.pos())
            if len(spots):
                index = int(spots[0].data())
                if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    self.point_deleted.emit(index)
                else:
                    self.point_selected.emit(index)
                event.accept()
                return
        super().mouseClickEvent(event)


class WaveformWindow(QMainWindow):
    def __init__(self, exchange_path: Path, payload: dict[str, object]) -> None:
        super().__init__()
        self.exchange_path = exchange_path
        self.params = {str(key): str(value) for key, value in dict(payload.get("params", {})).items()}
        self.points = parse_waveform_points(self.params)
        self.selected = 0
        self.history: list[list[WaveformPoint]] = []
        self.future: list[list[WaveformPoint]] = []
        self.drag_before: list[WaveformPoint] | None = None
        self.updating = False
        self.setWindowTitle("DP100 Waveform Editor")
        self.resize(1320, 780)
        self.setMinimumSize(1020, 620)
        self.setStyleSheet(STYLE)
        self._build_toolbar()
        self._build_ui()
        self._build_shortcuts()
        self.refresh(auto_range=True)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Waveform")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label, shortcut, callback in (
            ("Undo", QKeySequence.StandardKey.Undo, self.undo),
            ("Redo", QKeySequence.StandardKey.Redo, self.redo),
            ("Auto XY", QKeySequence("A"), self.auto_xy),
        ):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        for name in ("Pulse", "Ramp", "Staircase"):
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, value=name: self.apply_preset(value))
            toolbar.addAction(action)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)
        splitter.addWidget(self._editor_panel())

        self.view_box = EditorViewBox(self)
        self.plot = pg.PlotWidget(viewBox=self.view_box, background="#080d13")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.setLabel("bottom", "Time", units="ms")
        self.plot.setLabel("left", "Voltage", units="V")
        self.plot_item = self.plot.plot(pen=pg.mkPen("#fb7185", width=2.3))
        self.scatter = DraggablePoints()
        self.scatter.drag_started.connect(self.begin_point_drag_index)
        self.scatter.point_dragged.connect(self.drag_point_index)
        self.scatter.drag_finished.connect(self.end_point_drag)
        self.scatter.point_selected.connect(self.select_index)
        self.scatter.point_deleted.connect(self.delete_index)
        self.selected_scatter = pg.ScatterPlotItem(size=14, pen=pg.mkPen("#f8fafc", width=2), brush=pg.mkBrush("#fb7185"))
        self.selected_scatter.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.plot.addItem(self.scatter)
        self.plot.addItem(self.selected_scatter)
        self.cross_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#334155", style=Qt.PenStyle.DashLine))
        self.cross_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#334155", style=Qt.PenStyle.DashLine))
        self.plot.addItem(self.cross_v, ignoreBounds=True)
        self.plot.addItem(self.cross_h, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self.mouse_moved)
        splitter.addWidget(self.plot)
        splitter.addWidget(self._point_panel())
        splitter.setSizes([245, 760, 315])

        self.status = self.statusBar()
        self.status.showMessage("Point drag: edit  |  Double-click/Ctrl+click: add  |  Alt+click: delete  |  Background drag: pan  |  Wheel: zoom")

    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        return frame, layout

    def _editor_panel(self) -> QWidget:
        frame, layout = self._panel()
        frame.setMinimumWidth(280)
        title = QLabel("EDITOR")
        title.setObjectName("section")
        layout.addWidget(title)
        self.quantity = QComboBox()
        self.quantity.addItems(["Voltage", "Current"])
        self.quantity.currentTextChanged.connect(self.curve_changed)
        layout.addWidget(self.quantity)
        self.snap_enabled = QCheckBox("Snap to grid")
        self.snap_enabled.setChecked(True)
        self.smart_snap = QCheckBox("Smart snap when step is zero")
        self.smart_snap.setChecked(True)
        self.show_points = QCheckBox("Show sample points")
        self.show_points.setChecked(True)
        layout.addWidget(self.snap_enabled)
        layout.addWidget(self.smart_snap)
        layout.addWidget(self.show_points)
        self.show_points.toggled.connect(self.refresh)

        form = QFormLayout()
        self.snap_time = self._spin(0, 1e9, 1, 6, " ms")
        self.snap_voltage = self._spin(0, 65.535, 0.1, 6, " V")
        self.snap_current = self._spin(0, 65.535, 0.001, 6, " A")
        form.addRow("Snap time", self.snap_time)
        form.addRow("Snap voltage", self.snap_voltage)
        form.addRow("Snap current", self.snap_current)
        layout.addLayout(form)

        bounds = QLabel("BOUNDARIES")
        bounds.setObjectName("section")
        layout.addWidget(bounds)
        boundary_form = QFormLayout()
        self.duration = self._spin(0.001, 1e12, max(point.time_ms for point in self.points), 6, " ms")
        self.v_min = self._spin(0, 65.534, 0, 6, " V")
        self.v_max = self._spin(0.001, 65.535, max(1, max(point.voltage_v for point in self.points) * 1.15), 6, " V")
        self.i_max = self._spin(0.001, 65.535, max(0.001, max(point.current_a for point in self.points) * 1.25), 6, " A")
        boundary_form.addRow("Duration", self.duration)
        boundary_form.addRow("Voltage min", self.v_min)
        boundary_form.addRow("Voltage max", self.v_max)
        boundary_form.addRow("Current max", self.i_max)
        layout.addLayout(boundary_form)

        repeat_title = QLabel("OUTPUT")
        repeat_title.setObjectName("section")
        layout.addWidget(repeat_title)
        output_form = QFormLayout()
        self.repeat = self._spin(1, 1_000_000, float(self.params.get("count", "1")), 0, "")
        self.final_state = QComboBox()
        self.final_state.addItems(["off", "baseline"])
        self.final_state.setCurrentText(self.params.get("final_state", "off"))
        output_form.addRow("Repeat", self.repeat)
        output_form.addRow("Final state", self.final_state)
        layout.addLayout(output_form)
        layout.addStretch(1)
        return frame

    def _point_panel(self) -> QWidget:
        frame, layout = self._panel()
        frame.setMinimumWidth(340)
        title = QLabel("WAVEFORM POINTS")
        title.setObjectName("section")
        layout.addWidget(title)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time (ms)", "Voltage (V)", "Current (A)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.table_selected)
        self.table.itemChanged.connect(self.table_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        delete = QPushButton("Delete")
        add.clicked.connect(self.add_midpoint)
        delete.clicked.connect(self.delete_selected)
        row.addWidget(add)
        row.addWidget(delete)
        layout.addLayout(row)
        apply_button = QPushButton("Apply to Workflow")
        apply_button.setObjectName("apply")
        apply_button.clicked.connect(self.apply_and_close)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        layout.addWidget(apply_button)
        layout.addWidget(cancel)
        return frame

    def _spin(self, minimum: float, maximum: float, value: float, decimals: int, suffix: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        return spin

    def _build_shortcuts(self) -> None:
        delete = QAction(self)
        delete.setShortcut(QKeySequence.StandardKey.Delete)
        delete.triggered.connect(self.delete_selected)
        self.addAction(delete)

    def snapshot(self) -> list[WaveformPoint]:
        return list(self.points)

    def checkpoint(self) -> None:
        value = self.snapshot()
        if not self.history or self.history[-1] != value:
            self.history.append(value)
        self.future.clear()

    def undo(self) -> None:
        if not self.history:
            return
        self.future.append(self.snapshot())
        self.points = self.history.pop()
        self.selected = min(self.selected, len(self.points) - 1)
        self.refresh()

    def redo(self) -> None:
        if not self.future:
            return
        self.history.append(self.snapshot())
        self.points = self.future.pop()
        self.selected = min(self.selected, len(self.points) - 1)
        self.refresh()

    def refresh(self, checked: bool = False, *, auto_range: bool = False) -> None:
        del checked
        xs = [point.time_ms for point in self.points]
        ys = [self.active_value(point) for point in self.points]
        color = "#38bdf8" if self.quantity.currentText() == "Current" else "#fb7185"
        self.plot_item.setPen(pg.mkPen(color, width=2.3))
        self.scatter.setPen(pg.mkPen(color, width=1.5))
        self.selected_scatter.setBrush(pg.mkBrush(color))
        self.plot_item.setData(xs, ys)
        self.scatter.setData(
            xs if self.show_points.isChecked() else [],
            ys if self.show_points.isChecked() else [],
            data=list(range(len(self.points))) if self.show_points.isChecked() else [],
        )
        selected = self.points[self.selected]
        self.selected_scatter.setData([selected.time_ms], [self.active_value(selected)])
        self.updating = True
        self.table.setRowCount(len(self.points))
        for row, point in enumerate(self.points):
            for column, value in enumerate((point.time_ms, point.voltage_v, point.current_a)):
                item = self.table.item(row, column) or QTableWidgetItem()
                item.setText(f"{value:.9g}")
                self.table.setItem(row, column, item)
        self.table.selectRow(self.selected)
        self.updating = False
        if auto_range:
            QTimer.singleShot(0, self.auto_xy)

    def auto_xy(self) -> None:
        duration = max(self.duration.value(), self.points[-1].time_ms)
        self.plot.setXRange(0, duration, padding=0.04)
        if self.quantity.currentText() == "Current":
            self.plot.setYRange(0, max(self.i_max.value(), max(point.current_a for point in self.points)), padding=0.06)
        else:
            self.plot.setYRange(self.v_min.value(), max(self.v_max.value(), max(point.voltage_v for point in self.points)), padding=0.06)

    def curve_changed(self, name: str) -> None:
        is_current = name == "Current"
        self.plot.setLabel("left", name, units="A" if is_current else "V")
        self.refresh(auto_range=True)

    def active_value(self, point: WaveformPoint) -> float:
        return point.current_a if self.quantity.currentText() == "Current" else point.voltage_v

    def nearest_index(self, time_ms: float, voltage_v: float) -> int:
        x_range, y_range = self.view_box.viewRange()
        x_scale = max(1e-12, x_range[1] - x_range[0])
        y_scale = max(1e-12, y_range[1] - y_range[0])
        return min(range(len(self.points)), key=lambda index: ((self.points[index].time_ms - time_ms) / x_scale) ** 2 + ((self.active_value(self.points[index]) - voltage_v) / y_scale) ** 2)

    def select_nearest(self, time_ms: float, voltage_v: float) -> None:
        self.selected = self.nearest_index(time_ms, voltage_v)
        self.refresh()

    def select_index(self, index: int) -> None:
        self.selected = max(0, min(index, len(self.points) - 1))
        self.refresh()

    def add_point(self, time_ms: float, voltage_v: float) -> None:
        self.checkpoint()
        selected = self.points[self.selected]
        if self.quantity.currentText() == "Current":
            point = self.snapped_point(time_ms, selected.voltage_v, voltage_v)
        else:
            point = self.snapped_point(time_ms, voltage_v, selected.current_a)
        self.points = normalize_waveform_points([*self.points, point])
        self.selected = self.nearest_index(point.time_ms, self.active_value(point))
        self.refresh()

    def delete_nearest(self, time_ms: float, voltage_v: float) -> None:
        self.selected = self.nearest_index(time_ms, voltage_v)
        self.delete_selected()

    def delete_index(self, index: int) -> None:
        self.selected = index
        self.delete_selected()

    def add_midpoint(self) -> None:
        self.add_point(self.duration.value() / 2, self.points[self.selected].voltage_v)

    def delete_selected(self) -> None:
        if len(self.points) <= 2 or self.selected in {0, len(self.points) - 1}:
            return
        self.checkpoint()
        self.points.pop(self.selected)
        self.selected = min(self.selected, len(self.points) - 1)
        self.refresh()

    def begin_point_drag(self, time_ms: float, voltage_v: float) -> None:
        self.selected = self.nearest_index(time_ms, voltage_v)
        self.drag_before = self.snapshot()

    def begin_point_drag_index(self, index: int) -> None:
        self.selected = index
        self.drag_before = self.snapshot()

    def drag_point_index(self, index: int, time_ms: float, voltage_v: float) -> None:
        self.selected = index
        self.drag_point(time_ms, voltage_v)

    def drag_point(self, time_ms: float, voltage_v: float) -> None:
        current = self.points[self.selected]
        point = (
            self.snapped_point(time_ms, current.voltage_v, voltage_v)
            if self.quantity.currentText() == "Current"
            else self.snapped_point(time_ms, voltage_v, current.current_a)
        )
        if self.selected == 0:
            point = WaveformPoint(0, point.voltage_v, point.current_a)
        elif self.selected == len(self.points) - 1:
            point = WaveformPoint(self.duration.value(), point.voltage_v, point.current_a)
        else:
            minimum_time = self.points[self.selected - 1].time_ms + 1e-9
            maximum_time = self.points[self.selected + 1].time_ms - 1e-9
            point = WaveformPoint(max(minimum_time, min(maximum_time, point.time_ms)), point.voltage_v, point.current_a)
        self.points[self.selected] = point
        self.refresh()

    def end_point_drag(self) -> None:
        if self.drag_before is not None and self.drag_before != self.points:
            self.history.append(self.drag_before)
            self.future.clear()
        self.drag_before = None

    def snapped_point(self, time_ms: float, voltage_v: float, current_a: float) -> WaveformPoint:
        time_ms = max(0.0, min(self.duration.value(), time_ms))
        voltage_v = max(self.v_min.value(), min(self.v_max.value(), voltage_v))
        current_a = max(0.0, min(self.i_max.value(), current_a))
        if self.snap_enabled.isChecked():
            time_step = self.snap_time.value() or (self.smart_step(self.view_box.viewRange()[0]) if self.smart_snap.isChecked() else 0)
            amplitude_step = (
                self.snap_current.value() if self.quantity.currentText() == "Current" else self.snap_voltage.value()
            ) or (self.smart_step(self.view_box.viewRange()[1]) if self.smart_snap.isChecked() else 0)
            if time_step > 0:
                time_ms = round(time_ms / time_step) * time_step
            if amplitude_step > 0:
                if self.quantity.currentText() == "Current":
                    current_a = round(current_a / amplitude_step) * amplitude_step
                else:
                    voltage_v = round(voltage_v / amplitude_step) * amplitude_step
        return WaveformPoint(time_ms, voltage_v, current_a)

    @staticmethod
    def smart_step(view_range: list[float]) -> float:
        span = max(1e-12, view_range[1] - view_range[0])
        return 10 ** math.floor(math.log10(span / 20))

    def table_selected(self) -> None:
        if self.updating or not self.table.selectedItems():
            return
        self.selected = self.table.selectedItems()[0].row()
        selected = self.points[self.selected]
        self.selected_scatter.setData([selected.time_ms], [self.active_value(selected)])

    def table_changed(self, item: QTableWidgetItem) -> None:
        if self.updating:
            return
        try:
            value = float(item.text())
        except ValueError:
            self.refresh()
            return
        row, column = item.row(), item.column()
        point = self.points[row]
        values = [point.time_ms, point.voltage_v, point.current_a]
        values[column] = value
        self.checkpoint()
        self.points[row] = WaveformPoint(*values)
        try:
            self.points = normalize_waveform_points(self.points)
        except ValueError:
            self.undo()
            return
        self.selected = min(row, len(self.points) - 1)
        self.refresh()

    def apply_preset(self, name: str) -> None:
        self.checkpoint()
        peak_v = max(point.voltage_v for point in self.points)
        peak_i = max(point.current_a for point in self.points)
        self.points = preset_waveform(name, duration_ms=self.duration.value(), peak_voltage_v=peak_v, peak_current_a=peak_i)
        self.selected = 0
        self.refresh(auto_range=True)

    def mouse_moved(self, scene_position) -> None:
        if not self.plot.scene().sceneRect().contains(scene_position):
            return
        point = self.view_box.mapSceneToView(scene_position)
        self.cross_v.setPos(point.x())
        self.cross_h.setPos(point.y())
        unit = "A" if self.quantity.currentText() == "Current" else "V"
        self.status.showMessage(f"t={point.x():.9g} ms    {self.quantity.currentText()}={point.y():.9g} {unit}    |    Drag point to edit, double-click to add, Alt+click to delete")

    def apply_and_close(self) -> None:
        try:
            points = normalize_waveform_points(self.points)
        except ValueError as exc:
            QMessageBox.critical(self, "Waveform", str(exc))
            return
        if points[-1].time_ms > self.duration.value() + 1e-9:
            QMessageBox.critical(self, "Waveform", "The last point is outside the configured duration.")
            return
        if any(point.voltage_v > 65.535 or point.current_a > 65.535 for point in points):
            QMessageBox.critical(self, "Waveform", "DP100 limits are 65.535 V and 65.535 A.")
            return
        self.params.update(
            {
                "waveform_points": serialize_waveform_points(points),
                "period_ms": f"{points[-1].time_ms:g}",
                "peak_voltage_v": f"{max(point.voltage_v for point in points):g}",
                "peak_current_a": f"{max(point.current_a for point in points):g}",
                "count": str(int(self.repeat.value())),
                "final_state": self.final_state.currentText(),
            }
        )
        self.exchange_path.write_text(json.dumps({"accepted": True, "params": self.params}, indent=2) + "\n", encoding="utf-8")
        self.close()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m semi_auto_probe.qt_waveform_editor <exchange.json>")
    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pg.setConfigOptions(antialias=True, foreground="#8da0b3")
    window = WaveformWindow(path, payload)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
