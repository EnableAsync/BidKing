"""已显示物品逐件录入表格。

用于记录拍卖期间已经看到的物品: 颜色、占格、价值、名字。
用于辅助估价:
  - 自动统计已显示金/红格数 (回填 owned_gold_grids / owned_red_grids)
  - 统计已显示物品的总价 (显示在主窗口)
  - 配合当前紫色候选, 计算剩余金红格数

4 列:
  颜色 (QComboBox: 白绿/蓝/紫/金/红)
  占格 (QSpinBox)
  价值 (QSpinBox 千分位显示)
  × 删除按钮

emits items_changed: 任何编辑都会触发, 主窗口用来 autosave + 重算。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# 颜色键 ↔ 显示名映射
COLORS: list[tuple[str, str]] = [
    ("", ""),
    ("wg", "白绿"),
    ("blue", "蓝"),
    ("purple", "紫"),
    ("gold", "金"),
    ("red", "红"),
]
COLOR_KEYS = [k for k, _ in COLORS]
COLOR_LABELS = [lab for _, lab in COLORS]


def _key_to_label(key: str) -> str:
    for k, lab in COLORS:
        if k == key:
            return lab
    return ""


def _label_to_key(label: str) -> str:
    for k, lab in COLORS:
        if lab == label:
            return k
    return ""


COL_COLOR = 0
COL_GRIDS = 1
COL_VALUE = 2
COL_DELETE = 3


class _IntSpinDelegate(QStyledItemDelegate):
    def __init__(self, parent: QWidget | None = None, maximum: int = 100, group_sep: bool = False):
        super().__init__(parent)
        self._max = maximum
        self._group = group_sep

    def createEditor(self, parent, option, index):
        sb = QSpinBox(parent)
        sb.setRange(0, self._max)
        sb.setGroupSeparatorShown(self._group)
        sb.setAlignment(Qt.AlignmentFlag.AlignRight)
        return sb

    def setEditorData(self, editor, index):
        v = index.data(Qt.ItemDataRole.EditRole) or 0
        try:
            editor.setValue(int(v))
        except (TypeError, ValueError):
            editor.setValue(0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class _ColorDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItems(COLOR_LABELS)
        return cb

    def setEditorData(self, editor, index):
        v = index.data(Qt.ItemDataRole.EditRole) or ""
        i = COLOR_LABELS.index(v) if v in COLOR_LABELS else 0
        editor.setCurrentIndex(i)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class RevealedItemsTable(QWidget):
    items_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._suspend_signals = False
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["颜色", "占格", "价值", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_COLOR, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_GRIDS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_DELETE, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_COLOR, 64)
        self.table.setColumnWidth(COL_GRIDS, 64)
        self.table.setColumnWidth(COL_DELETE, 36)

        self.table.setItemDelegateForColumn(COL_COLOR, _ColorDelegate(self))
        self.table.setItemDelegateForColumn(COL_GRIDS, _IntSpinDelegate(self, maximum=99))
        self.table.setItemDelegateForColumn(
            COL_VALUE, _IntSpinDelegate(self, maximum=99_999_999, group_sep=True)
        )

        self.table.cellChanged.connect(self._on_cell_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)

        add_btn = QPushButton("+ 添加已显示物品", self)
        add_btn.clicked.connect(lambda: self._append_empty_row(emit=True))
        layout.addWidget(add_btn)

        self._append_empty_row(emit=False)

    def _append_empty_row(self, emit: bool = True) -> int:
        row = self.table.rowCount()
        self._suspend_signals = True
        self.table.insertRow(row)

        color_item = QTableWidgetItem("")
        self.table.setItem(row, COL_COLOR, color_item)

        grids_item = QTableWidgetItem()
        grids_item.setData(Qt.ItemDataRole.EditRole, 0)
        grids_item.setData(Qt.ItemDataRole.DisplayRole, "")
        self.table.setItem(row, COL_GRIDS, grids_item)

        value_item = QTableWidgetItem()
        value_item.setData(Qt.ItemDataRole.EditRole, 0)
        value_item.setData(Qt.ItemDataRole.DisplayRole, "")
        self.table.setItem(row, COL_VALUE, value_item)

        delete_btn = QPushButton("×")
        delete_btn.setFixedWidth(28)
        delete_btn.setToolTip("删除该行")
        delete_btn.clicked.connect(lambda _=False, b=delete_btn: self._delete_button_clicked(b))
        self.table.setCellWidget(row, COL_DELETE, delete_btn)
        self._suspend_signals = False

        if emit:
            self.items_changed.emit()
        return row

    def _delete_button_clicked(self, btn: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, COL_DELETE) is btn:
                self.table.removeRow(row)
                if self.table.rowCount() == 0:
                    self._append_empty_row(emit=False)
                self.items_changed.emit()
                return

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._suspend_signals:
            return
        if col in (COL_GRIDS, COL_VALUE):
            item = self.table.item(row, col)
            if item is not None:
                iv = _int_of(item)
                self._suspend_signals = True
                if iv == 0:
                    item.setData(Qt.ItemDataRole.DisplayRole, "")
                else:
                    if col == COL_VALUE:
                        item.setData(Qt.ItemDataRole.DisplayRole, f"{iv:,}")
                    else:
                        item.setData(Qt.ItemDataRole.DisplayRole, str(iv))
                self._suspend_signals = False

        # 末行的占格或价值被填了, 自动补一空行
        if col in (COL_GRIDS, COL_VALUE) and row == self.table.rowCount() - 1:
            iv = _int_of(self.table.item(row, col))
            if iv > 0:
                self._append_empty_row(emit=False)

        self.items_changed.emit()

    # ---------- 数据访问 ----------

    def items(self) -> list[dict[str, Any]]:
        """返回非空行列表 (占格 > 0 或 价值 > 0 才算非空)。

        每项: {color: 颜色 key, grids: int, value: int, name: str|None}
        """
        result: list[dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            grids = _int_of(self.table.item(row, COL_GRIDS))
            value = _int_of(self.table.item(row, COL_VALUE))
            if grids == 0 and value == 0:
                continue
            color_item = self.table.item(row, COL_COLOR)
            color_label = color_item.text() if color_item is not None else ""
            color_key = _label_to_key(color_label)
            result.append(
                {
                    "color": color_key or None,
                    "grids": grids,
                    "value": value,
                }
            )
        return result

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self._suspend_signals = True
        self.table.setRowCount(0)
        for item in items:
            row = self._append_empty_row(emit=False)
            color_key = item.get("color") or ""
            self.table.item(row, COL_COLOR).setText(_key_to_label(color_key))
            self.table.item(row, COL_GRIDS).setData(
                Qt.ItemDataRole.EditRole, int(item.get("grids") or 0)
            )
            self.table.item(row, COL_VALUE).setData(
                Qt.ItemDataRole.EditRole, int(item.get("value") or 0)
            )
        if self.table.rowCount() == 0:
            self._append_empty_row(emit=False)
        else:
            last = self.table.rowCount() - 1
            if (
                _int_of(self.table.item(last, COL_GRIDS)) > 0
                or _int_of(self.table.item(last, COL_VALUE)) > 0
            ):
                self._append_empty_row(emit=False)
        self._suspend_signals = False
        for row in range(self.table.rowCount()):
            self._on_cell_changed(row, COL_GRIDS)
            self._on_cell_changed(row, COL_VALUE)

    def summary(self) -> dict[str, Any]:
        """聚合统计:
        {
          per_color: {wg: {grids, value}, blue: ..., purple: ..., gold: ..., red: ...},
          total_grids: int, total_value: int,
        }
        """
        per_color: dict[str, dict[str, int]] = {
            k: {"grids": 0, "value": 0} for k in COLOR_KEYS if k
        }
        total_grids = 0
        total_value = 0
        for it in self.items():
            color = it.get("color")
            g = int(it.get("grids") or 0)
            v = int(it.get("value") or 0)
            total_grids += g
            total_value += v
            if color and color in per_color:
                per_color[color]["grids"] += g
                per_color[color]["value"] += v
        return {
            "per_color": per_color,
            "total_grids": total_grids,
            "total_value": total_value,
        }


def _int_of(item: QTableWidgetItem | None) -> int:
    """读取单元格存的整数。

    注意 QTableWidgetItem 中 EditRole 和 DisplayRole 共享存储位,
    因此含千分位逗号的格式化字符串 也要能解析。
    """
    if item is None:
        return 0
    v = item.data(Qt.ItemDataRole.EditRole)
    if v is None or v == "":
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0
