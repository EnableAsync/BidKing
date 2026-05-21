"""主窗口。

布局: 单窗口分区 (草图见设计讨论)。
- 顶部: 策略 + session 状态 + 操作按钮
- 元数据: 地图 / 英雄
- 输入: T / B / WG / 紫均 / 紫数预估 / 4个单格价
- 输出: 候选 (a, b) 列表 + 总估值 (动态联动)
- 出价
- 真实数据: 白绿/蓝/紫/金 聚合 + 红逐件
- 注释 + 完成/删除按钮 + 状态栏

行为:
- autosave (任何编辑都立即落盘到当前 record_id)
- 启动: 自动加载最近一条 draft，没有则新建空白
- 错误展示: 字段标红 + 状态栏
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..storage.config import Config, KEY_OCR_SAVE_SCREENSHOT
from ..storage.records import (
    RecordStore,
    empty_record,
    new_session_id,
)
from ..strategies.grid_actuarial import GridActuarial
from ..strategies.base import StrategyBase
from .ocr_worker import OCRWorker
from .widgets.red_items_table import RedItemsTable
from .widgets.screenshot import ScreenshotWidget


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
RECORDS_PATH = PROJECT_DIR / "records.jsonl"
CONFIG_PATH = PROJECT_DIR / "config.json"


def _load_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_int_spin(maximum: int = 99_999_999, group_sep: bool = True, width: int = 120) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(0, maximum)
    sb.setGroupSeparatorShown(group_sep)
    sb.setAlignment(Qt.AlignmentFlag.AlignRight)
    sb.setMaximumWidth(width)
    return sb


def _make_money_spin(width: int = 160) -> QSpinBox:
    sb = _make_int_spin(maximum=999_999_999, group_sep=True, width=width)
    return sb


def _make_float_spin(decimals: int = 2, maximum: float = 999.99, width: int = 100) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setDecimals(decimals)
    sb.setRange(0.0, maximum)
    sb.setSingleStep(0.01)
    sb.setAlignment(Qt.AlignmentFlag.AlignRight)
    sb.setMaximumWidth(width)
    return sb


def _fmt_money(v: int | float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"¥ {int(v):,}"
    except (TypeError, ValueError):
        return "—"


# 品质配色 (bg, fg/border)
QUALITY_COLORS: dict[str, tuple[str, str]] = {
    "wg":     ("#e8f5e9", "#2e7d32"),   # 白+绿
    "blue":   ("#e3f2fd", "#1565c0"),
    "purple": ("#f3e5f5", "#7b1fa2"),
    "gold":   ("#fff8e1", "#f57f17"),
    "red":    ("#ffebee", "#c62828"),
    "jr":     ("#ffe0b2", "#e65100"),   # 金红合并
}


def _tint_spin(sb: QWidget, key: str) -> None:
    bg, _ = QUALITY_COLORS[key]
    sb.setStyleSheet(f"QAbstractSpinBox {{ background-color: {bg}; }}")


def _tint_groupbox(box: QGroupBox, key: str) -> None:
    bg, fg = QUALITY_COLORS[key]
    box.setStyleSheet(
        f"""
        QGroupBox {{
            border: 1px solid {fg};
            border-radius: 4px;
            margin-top: 10px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            color: {fg};
            background-color: {bg};
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 8px;
            padding: 0 6px;
        }}
        """
    )


def _set_error(widget: QWidget, on: bool) -> None:
    """红框标记错误字段"""
    widget.setStyleSheet("border: 2px solid #e53935;" if on else "")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BidKing — 竞拍之王估价")
        self.resize(1400, 800)

        self.config = Config(CONFIG_PATH)
        self.store = RecordStore(RECORDS_PATH)

        self.maps = _load_json(DATA_DIR / "maps.json")
        self.heroes = _load_json(DATA_DIR / "heroes.json")

        self.strategies: dict[str, StrategyBase] = {
            GridActuarial.name: GridActuarial(),
        }
        self.current_strategy: StrategyBase = self.strategies[GridActuarial.name]

        self.current_record: dict[str, Any] = {}
        self.current_session_id: str = ""

        # 防抖: input 改动后 50ms 内多次只触发一次 autosave
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(50)
        self._save_timer.timeout.connect(self._do_save)

        self._loading = False  # 加载记录时屏蔽 autosave

        # OCR worker 状态
        self._ocr_worker: OCRWorker | None = None
        self._ocr_pending: bool = False  # 当前 worker 还在跑时被点了一次, 需要 worker 结束后重启
        self._ocr_discard_current: bool = False  # 当前 worker 的结果是否要丢弃

        self._build_ui()
        self._load_initial_record()

    # ---------- UI 构造 ----------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        outer_v = QVBoxLayout(central)
        outer_v.setContentsMargins(12, 12, 12, 12)
        outer_v.setSpacing(8)

        outer_v.addWidget(self._build_topbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_w = QWidget()
        left_v = QVBoxLayout(left_w)
        left_v.setContentsMargins(0, 0, 6, 0)
        left_v.setSpacing(8)
        left_title = QLabel("估价 (出价前)")
        left_title.setStyleSheet("font-weight: bold; font-size: 12pt; padding: 4px 0;")
        left_v.addWidget(left_title)
        left_v.addWidget(self._build_metadata_box())
        left_v.addWidget(self._build_inputs_box())
        left_v.addWidget(self._build_outputs_box())
        left_v.addStretch()
        left_scroll.setWidget(left_w)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_w = QWidget()
        right_v = QVBoxLayout(right_w)
        right_v.setContentsMargins(6, 0, 0, 0)
        right_v.setSpacing(8)
        right_title = QLabel("事后录入 (拍卖结束后)")
        right_title.setStyleSheet("font-weight: bold; font-size: 12pt; padding: 4px 0;")
        right_v.addWidget(right_title)
        right_v.addWidget(self._build_actual_box())
        right_v.addWidget(self._build_note_box())
        right_v.addWidget(self._build_bid_box())
        right_v.addStretch()
        right_scroll.setWidget(right_w)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1080, 300])
        outer_v.addWidget(splitter, stretch=1)

        outer_v.addWidget(self._build_bottom_buttons())

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

    def _build_topbar(self) -> QWidget:
        box = QGroupBox("当前游戏")
        h = QHBoxLayout(box)

        h.addWidget(QLabel("策略:"))
        self.strategy_combo = QComboBox()
        for name in self.strategies:
            self.strategy_combo.addItem(name)
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        h.addWidget(self.strategy_combo)

        h.addSpacing(20)

        self.session_label = QLabel("(未开始)")
        h.addWidget(self.session_label, stretch=1)

        self.btn_new_game = QPushButton("开始新游戏")
        self.btn_new_game.clicked.connect(self._on_new_game)
        h.addWidget(self.btn_new_game)

        self.btn_new_auction = QPushButton("新一局拍卖")
        self.btn_new_auction.clicked.connect(self._on_new_auction)
        h.addWidget(self.btn_new_auction)

        self.btn_history = QPushButton("历史记录")
        self.btn_history.clicked.connect(self._on_open_history)
        h.addWidget(self.btn_history)

        return box

    def _build_metadata_box(self) -> QWidget:
        box = QGroupBox("本场元数据")
        h = QHBoxLayout(box)
        h.setSpacing(8)

        self.map_combo = QComboBox()
        self.map_combo.setEditable(True)
        self.map_combo.setMinimumWidth(220)
        for m in self.maps:
            self.map_combo.addItem(f"{m['id']} {m['name']} ({m['tier']})", m["id"])
        self.map_combo.setCurrentIndex(-1)
        self.map_combo.currentIndexChanged.connect(self._on_field_changed)
        self.map_combo.editTextChanged.connect(self._on_field_changed)

        self.hero_combo = QComboBox()
        self.hero_combo.setEditable(True)
        self.hero_combo.setMinimumWidth(220)
        for hero in self.heroes:
            self.hero_combo.addItem(
                f"{hero['id']} {hero['name']} [{hero['tier']}]", hero["id"]
            )
        self.hero_combo.setCurrentIndex(-1)
        self.hero_combo.currentIndexChanged.connect(self._on_field_changed)
        self.hero_combo.editTextChanged.connect(self._on_field_changed)

        h.addWidget(QLabel("地图:"))
        h.addWidget(self.map_combo)
        h.addSpacing(16)
        h.addWidget(QLabel("英雄 (一局沿用):"))
        h.addWidget(self.hero_combo)
        h.addStretch()
        return box

    def _build_inputs_box(self) -> QWidget:
        box = QGroupBox("输入 (数格子精算法) — 任意输入越多, 估值范围越窄")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.in_T = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_B = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_WG = _make_int_spin(maximum=999, group_sep=False, width=80)
        # 紫色五件套 (全部可选)
        self.in_purple_avg = _make_float_spin(decimals=2, maximum=99.99, width=80)
        self.in_purple_count_est = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_purple_total_grids = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_purple_count = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_purple_total_value = _make_money_spin(width=130)
        # 金色五件套
        self.in_gold_avg = _make_float_spin(decimals=2, maximum=99.99, width=80)
        self.in_gold_count_est = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_gold_total_grids = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_gold_count = _make_int_spin(maximum=999, group_sep=False, width=80)
        self.in_gold_total_value = _make_money_spin(width=130)

        _tint_spin(self.in_B, "blue")
        _tint_spin(self.in_WG, "wg")
        for w in (self.in_purple_avg, self.in_purple_count_est,
                  self.in_purple_total_grids, self.in_purple_count,
                  self.in_purple_total_value):
            _tint_spin(w, "purple")
        for w in (self.in_gold_avg, self.in_gold_count_est,
                  self.in_gold_total_grids, self.in_gold_count,
                  self.in_gold_total_value):
            _tint_spin(w, "gold")

        defaults = self.config.get_strategy_defaults(
            self.current_strategy.name, self.current_strategy.defaults
        )

        self.in_v_wg = _make_money_spin()
        self.in_v_b = _make_money_spin()
        self.in_v_p = _make_money_spin()
        self.in_v_jr = _make_money_spin()
        self.in_v_g = _make_money_spin()
        self.in_v_r = _make_money_spin()
        _tint_spin(self.in_v_wg, "wg")
        _tint_spin(self.in_v_b, "blue")
        _tint_spin(self.in_v_p, "purple")
        _tint_spin(self.in_v_jr, "jr")
        _tint_spin(self.in_v_g, "gold")
        _tint_spin(self.in_v_r, "red")
        self.in_v_wg.setValue(int(defaults.get("v_wg", 0)))
        self.in_v_b.setValue(int(defaults.get("v_b", 0)))
        self.in_v_p.setValue(int(defaults.get("v_p", 0)))
        self.in_v_jr.setValue(int(defaults.get("v_jr", 0)))
        self.in_v_g.setValue(int(defaults.get("v_g", 0)))
        self.in_v_r.setValue(int(defaults.get("v_r", 0)))
        self.in_purple_count_est.setValue(int(defaults.get("purple_count_est", 0)))
        self.in_gold_count_est.setValue(int(defaults.get("gold_count_est", 0)))

        for w in (
            self.in_T, self.in_B, self.in_WG,
            self.in_purple_avg, self.in_purple_count_est,
            self.in_purple_total_grids, self.in_purple_count, self.in_purple_total_value,
            self.in_gold_avg, self.in_gold_count_est,
            self.in_gold_total_grids, self.in_gold_count, self.in_gold_total_value,
            self.in_v_wg, self.in_v_b, self.in_v_p, self.in_v_jr, self.in_v_g, self.in_v_r,
        ):
            w.valueChanged.connect(self._on_field_changed)

        # 从游戏截图 OCR 自动填充 (插在最上面，紧贴它要填的字段)
        form.addRow(self._build_ocr_row())

        # 基础格数
        basic_box = QGroupBox("总格数 / 蓝色 / 白绿")
        basic_h = QHBoxLayout(basic_box)
        basic_h.addWidget(QLabel("总格数:"))
        basic_h.addWidget(self.in_T)
        basic_h.addSpacing(12)
        basic_h.addWidget(QLabel("蓝色格数:"))
        basic_h.addWidget(self.in_B)
        basic_h.addSpacing(12)
        basic_h.addWidget(QLabel("白绿格数:"))
        basic_h.addWidget(self.in_WG)
        basic_h.addStretch()
        form.addRow(basic_box)

        self.in_purple_avg.setToolTip("紫色平均格数 c_p (优品均格 道具, 1000银)")
        self.in_purple_count_est.setToolTip("紫色物品数预估 b_est, 主观判断")
        self.in_purple_total_grids.setToolTip("紫色总格数 a_p (优品扫描 道具, 2500银)")
        self.in_purple_count.setToolTip("紫色物品数 b_p (优品存量 道具, 2500银)")
        self.in_purple_total_value.setToolTip("紫色总价值 (优品估价 道具, 2500银)")
        self.in_gold_avg.setToolTip("金色平均格数 c_g (极品均格 道具, 10000银)")
        self.in_gold_count_est.setToolTip("金色物品数预估 b_est, 主观判断")
        self.in_gold_total_grids.setToolTip("金色总格数 a_g (极品扫描 道具, 10000银)")
        self.in_gold_count.setToolTip("金色物品数 b_g (极品存量 道具, 10000银)")
        self.in_gold_total_value.setToolTip("金色总价值 (极品估价 道具, 10000银)")

        # 紫色 (一行排列)
        p_box = QGroupBox("紫色 (任意组合即可反推; 给越多, 范围越窄)")
        _tint_groupbox(p_box, "purple")
        p_h = QHBoxLayout(p_box)
        p_h.addWidget(QLabel("平均格数:"))
        p_h.addWidget(self.in_purple_avg)
        p_h.addSpacing(8)
        p_h.addWidget(QLabel("预估件数:"))
        p_h.addWidget(self.in_purple_count_est)
        p_h.addSpacing(8)
        p_h.addWidget(QLabel("总格数:"))
        p_h.addWidget(self.in_purple_total_grids)
        p_h.addSpacing(8)
        p_h.addWidget(QLabel("件数:"))
        p_h.addWidget(self.in_purple_count)
        p_h.addSpacing(8)
        p_h.addWidget(QLabel("总价值:"))
        p_h.addWidget(self.in_purple_total_value)
        p_h.addStretch()
        form.addRow(p_box)

        # 金色 (一行排列)
        g_box = QGroupBox("金色 (可选; 任意组合)")
        _tint_groupbox(g_box, "gold")
        g_h = QHBoxLayout(g_box)
        g_h.addWidget(QLabel("平均格数:"))
        g_h.addWidget(self.in_gold_avg)
        g_h.addSpacing(8)
        g_h.addWidget(QLabel("预估件数:"))
        g_h.addWidget(self.in_gold_count_est)
        g_h.addSpacing(8)
        g_h.addWidget(QLabel("总格数:"))
        g_h.addWidget(self.in_gold_total_grids)
        g_h.addSpacing(8)
        g_h.addWidget(QLabel("件数:"))
        g_h.addWidget(self.in_gold_count)
        g_h.addSpacing(8)
        g_h.addWidget(QLabel("总价值:"))
        g_h.addWidget(self.in_gold_total_value)
        g_h.addStretch()
        form.addRow(g_box)

        # 单格估价
        price_box = QGroupBox("单格估价 (持久化)")
        price_h = QHBoxLayout(price_box)
        for lab, w in (
            ("白绿", self.in_v_wg), ("蓝", self.in_v_b), ("紫", self.in_v_p),
            ("金红混", self.in_v_jr), ("金", self.in_v_g), ("红", self.in_v_r),
        ):
            price_h.addWidget(QLabel(lab + ":"))
            w.setMaximumWidth(110)
            price_h.addWidget(w)
            price_h.addSpacing(4)
        price_h.addStretch()
        form.addRow(price_box)

        return box

    def _build_ocr_row(self) -> QWidget:
        """输入区顶部的「从游戏截图自动填充」按钮 + 「保存原图」开关。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        self.btn_ocr = QPushButton("📷 从游戏截图自动填充")
        self.btn_ocr.setStyleSheet(
            "font-weight: bold; padding: 6px 14px; background: #e3f2fd;"
            " border: 1px solid #1565c0; border-radius: 4px;"
        )
        self.btn_ocr.setToolTip(
            "自动截取《竞拍之王》游戏窗口，识别并填入总格数、蓝色格数、"
            "白绿格数、紫色平均占用格数 4 项。\n"
            "已识别到的字段会覆盖输入框；未识别到的字段保持不变。\n"
            "识别过程中再次点击会取消上一次并重新识别。"
        )
        self.btn_ocr.clicked.connect(self._on_ocr_button_clicked)
        h.addWidget(self.btn_ocr)

        self.cb_save_screenshot = QCheckBox("保存原图")
        self.cb_save_screenshot.setToolTip(
            "勾选后，每次自动填充会把游戏原始截图保存到"
            " screenshots/<record_id>-input.png，便于事后核对识别结果"
            "或攒数据集训练模型。"
        )
        self.cb_save_screenshot.setChecked(
            bool(self.config.get(KEY_OCR_SAVE_SCREENSHOT, True))
        )
        self.cb_save_screenshot.toggled.connect(self._on_save_screenshot_toggled)
        h.addWidget(self.cb_save_screenshot)
        h.addStretch()

        return w

    def _build_outputs_box(self) -> QWidget:
        box = QGroupBox("输出 (动态联动)")
        v = QVBoxLayout(box)

        hint = QLabel(
            "候选解释: 同一个平均格数可能对应多组 (总格数, 物品数)。"
            "输入越多, 候选越少, 价值范围越窄。"
        )
        hint.setStyleSheet("color: #555; font-size: 9pt;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 价值范围 (最显眼)
        self.lbl_value_range = QLabel("预估仓库价值: —")
        self.lbl_value_range.setStyleSheet("font-weight: bold; font-size: 14pt;")
        v.addWidget(self.lbl_value_range)

        # 当前选中组合的明细
        self.lbl_selected_detail = QLabel("当前选: —")
        self.lbl_selected_detail.setStyleSheet("color: #333;")
        v.addWidget(self.lbl_selected_detail)

        # 紫色 + 金色 候选 并排
        cands_h = QHBoxLayout()

        purple_col = QVBoxLayout()
        purple_col.setSpacing(2)
        p_title = QLabel("紫色候选")
        p_title.setStyleSheet("color: #7b1fa2; font-weight: bold;")
        purple_col.addWidget(p_title)
        self.purple_candidates_group = QButtonGroup(self)
        self.purple_candidates_group.setExclusive(True)
        self.purple_candidates_group.idToggled.connect(
            lambda btn_id, checked: self._on_candidate_toggled("purple", btn_id, checked)
        )
        self.purple_candidates_container = QVBoxLayout()
        self.purple_candidates_container.setSpacing(2)
        purple_col.addLayout(self.purple_candidates_container)
        purple_col.addStretch()
        self._cached_purple_candidates: list[dict[str, Any]] = []

        gold_col = QVBoxLayout()
        gold_col.setSpacing(2)
        g_title = QLabel("金色候选")
        g_title.setStyleSheet("color: #f57f17; font-weight: bold;")
        gold_col.addWidget(g_title)
        self.gold_candidates_group = QButtonGroup(self)
        self.gold_candidates_group.setExclusive(True)
        self.gold_candidates_group.idToggled.connect(
            lambda btn_id, checked: self._on_candidate_toggled("gold", btn_id, checked)
        )
        self.gold_candidates_container = QVBoxLayout()
        self.gold_candidates_container.setSpacing(2)
        gold_col.addLayout(self.gold_candidates_container)
        gold_col.addStretch()
        self._cached_gold_candidates: list[dict[str, Any]] = []

        cands_h.addLayout(purple_col, stretch=1)
        cands_h.addLayout(gold_col, stretch=1)
        v.addLayout(cands_h)

        # 我的出价已移到右列 (注释下面)

        self.output_errors_label = QLabel("")
        self.output_errors_label.setStyleSheet("color: #e53935;")
        self.output_errors_label.setWordWrap(True)
        v.addWidget(self.output_errors_label)

        return box

    def _build_actual_box(self) -> QWidget:
        box = QGroupBox("真实结果 (事后填写)")
        v = QVBoxLayout(box)

        # 仓库总价
        total_form = QFormLayout()
        self.act_total_value = _make_money_spin(width=180)
        self.act_total_value.valueChanged.connect(self._on_field_changed)
        total_form.addRow("仓库总价:", self.act_total_value)
        v.addLayout(total_form)

        # 截图
        screenshot_label = QLabel("结算截图 (含总价、物品、玩家盈亏)")
        screenshot_label.setStyleSheet("color: #555; padding-top: 6px;")
        v.addWidget(screenshot_label)
        self.screenshot_widget = ScreenshotWidget(PROJECT_DIR / "screenshots", parent=self)
        self.screenshot_widget.path_changed.connect(self._on_field_changed)
        v.addWidget(self.screenshot_widget)

        return box

    def _build_note_box(self) -> QWidget:
        box = QGroupBox("注释")
        v = QVBoxLayout(box)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("可记录对手出价、关键判断、复盘要点等")
        self.note_edit.setMinimumHeight(180)
        self.note_edit.textChanged.connect(self._on_field_changed)
        v.addWidget(self.note_edit)
        return box

    def _build_bid_box(self) -> QWidget:
        box = QGroupBox("我的出价")
        h = QHBoxLayout(box)
        self.in_bid = _make_money_spin(width=180)
        self.in_bid.valueChanged.connect(self._on_field_changed)
        h.addWidget(self.in_bid)
        h.addStretch()
        return box

    def _build_bottom_buttons(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("status: draft")
        h.addWidget(self.status_label)
        h.addStretch()

        self.btn_complete = QPushButton("标记完成 (纳入数据集)")
        self.btn_complete.clicked.connect(self._on_mark_complete)
        h.addWidget(self.btn_complete)

        self.btn_revert = QPushButton("回滚到 draft")
        self.btn_revert.clicked.connect(self._on_revert_draft)
        h.addWidget(self.btn_revert)

        self.btn_delete = QPushButton("删除本条")
        self.btn_delete.clicked.connect(self._on_delete)
        h.addWidget(self.btn_delete)

        return w

    # ---------- 加载/切换记录 ----------

    def _load_initial_record(self) -> None:
        draft = self.store.latest_draft()
        if draft:
            self.current_record = draft
            self.current_session_id = draft.get("session_id") or new_session_id()
            self._load_record_into_ui(draft)
        else:
            self._start_fresh_record(new_session=True)

    def _start_fresh_record(self, new_session: bool) -> None:
        if new_session or not self.current_session_id:
            self.current_session_id = new_session_id()
        prev_hero = self.store.latest_hero_id(self.current_session_id) if not new_session else None
        rec = empty_record(self.current_strategy.name, self.current_session_id, hero_id=prev_hero)
        self.current_record = rec
        self._load_record_into_ui(rec)
        self._schedule_save()

    def _load_record_into_ui(self, rec: dict[str, Any]) -> None:
        self._loading = True
        try:
            inputs = rec.get("inputs", {})
            self.in_T.setValue(int(inputs.get("T") or 0))
            self.in_B.setValue(int(inputs.get("B") or 0))
            self.in_WG.setValue(int(inputs.get("WG") or 0))
            self.in_purple_avg.setValue(float(inputs.get("purple_avg") or 0.0))
            self.in_purple_count_est.setValue(int(inputs.get("purple_count_est") or 0))
            self.in_purple_total_grids.setValue(int(inputs.get("purple_total_grids") or 0))
            self.in_purple_count.setValue(int(inputs.get("purple_count") or 0))
            self.in_purple_total_value.setValue(int(inputs.get("purple_total_value") or 0))
            self.in_gold_avg.setValue(float(inputs.get("gold_avg") or 0.0))
            self.in_gold_count_est.setValue(int(inputs.get("gold_count_est") or 0))
            self.in_gold_total_grids.setValue(int(inputs.get("gold_total_grids") or 0))
            self.in_gold_count.setValue(int(inputs.get("gold_count") or 0))
            self.in_gold_total_value.setValue(int(inputs.get("gold_total_value") or 0))

            defaults = self.config.get_strategy_defaults(
                self.current_strategy.name, self.current_strategy.defaults
            )
            self.in_v_wg.setValue(int(inputs.get("v_wg") or defaults.get("v_wg") or 0))
            self.in_v_b.setValue(int(inputs.get("v_b") or defaults.get("v_b") or 0))
            self.in_v_p.setValue(int(inputs.get("v_p") or defaults.get("v_p") or 0))
            self.in_v_jr.setValue(int(inputs.get("v_jr") or defaults.get("v_jr") or 0))
            self.in_v_g.setValue(int(inputs.get("v_g") or defaults.get("v_g") or 0))
            self.in_v_r.setValue(int(inputs.get("v_r") or defaults.get("v_r") or 0))

            map_id = rec.get("map_id")
            self._set_combo_by_id(self.map_combo, map_id)
            hero_id = rec.get("hero_id")
            self._set_combo_by_id(self.hero_combo, hero_id)

            self.in_bid.setValue(int(rec.get("bid") or 0))

            actual = rec.get("actual", {})
            self.act_total_value.setValue(int(actual.get("total_value") or 0))
            screenshot_path = actual.get("screenshot_path") or ""
            self.screenshot_widget.set_path(screenshot_path)

            self.note_edit.setPlainText(rec.get("note") or "")
        finally:
            self._loading = False
        self._refresh_session_label()
        self._refresh_status_label()
        self._recompute_outputs()

    def _set_combo_by_id(self, combo: QComboBox, target_id: Any) -> None:
        with QSignalBlocker(combo):
            if target_id is None:
                combo.setCurrentIndex(-1)
                combo.setEditText("")
                return
            for i in range(combo.count()):
                if combo.itemData(i) == target_id:
                    combo.setCurrentIndex(i)
                    return
            combo.setEditText(str(target_id))

    def _refresh_session_label(self) -> None:
        sess = self.current_session_id or "(无)"
        same = [r for r in self.store.all() if r.get("session_id") == self.current_session_id]
        n = len(same)
        hero_id = self.current_record.get("hero_id")
        hero_name = self._hero_name_of(hero_id)
        self.session_label.setText(f"session {sess} | 英雄 {hero_name} | 已 {n} 场")

    def _refresh_status_label(self) -> None:
        s = self.current_record.get("status", "draft")
        self.status_label.setText(f"status: {s}")

    def _hero_name_of(self, hero_id: Any) -> str:
        if hero_id is None:
            return "—"
        for h in self.heroes:
            if h["id"] == hero_id:
                return f"{h['id']} {h['name']}"
        return str(hero_id)

    # ---------- 字段变化 → 重算 + 防抖保存 ----------

    def _on_field_changed(self, *args: Any) -> None:
        if self._loading:
            return
        self._recompute_outputs()
        self._save_timer.start()

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _recompute_outputs(self) -> None:
        inputs = self._collect_inputs()
        result = self.current_strategy.compute(inputs)

        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        msg_parts = []
        if errors:
            msg_parts.append("⚠ " + " | ".join(errors))
        if warnings:
            msg_parts.append("注意: " + " | ".join(warnings))
        self.output_errors_label.setText("\n".join(msg_parts))

        hard_error = bool(errors)
        _set_error(self.in_T, hard_error and "总格数" in "".join(errors))
        _set_error(self.in_B, hard_error and "蓝" in "".join(errors))
        _set_error(self.in_WG, hard_error and "白绿" in "".join(errors))
        _set_error(self.in_purple_avg, any("紫色平均" in e or "紫色输入" in e for e in errors))
        _set_error(self.in_gold_avg, any("金色平均" in e or "金色输入" in e for e in errors))

        # 渲染两组候选
        self._render_candidate_group(
            "purple",
            result.get("purple_candidates", []),
            self.purple_candidates_group,
            self.purple_candidates_container,
            self._cached_purple_candidates,
        )
        self._cached_purple_candidates = list(result.get("purple_candidates", []))

        self._render_candidate_group(
            "gold",
            result.get("gold_candidates", []),
            self.gold_candidates_group,
            self.gold_candidates_container,
            self._cached_gold_candidates,
        )
        self._cached_gold_candidates = list(result.get("gold_candidates", []))

        # 更新范围 + 当前选中明细
        self._update_value_range_label(result.get("value_range"))
        self._update_selected_detail()

        if errors:
            self.status_bar.showMessage("⚠ " + " ; ".join(errors), 4000)
        else:
            self.status_bar.clearMessage()

    def _render_candidate_group(
        self,
        kind: str,
        candidates: list[dict[str, Any]],
        group: QButtonGroup,
        container: QVBoxLayout,
        cached: list[dict[str, Any]],
    ) -> None:
        current_sel = group.checkedId()
        if current_sel < 0:
            key = f"selected_{kind}_idx"
            current_sel = int(self.current_record.get("predicted", {}).get(key, 0) or 0)
        if not candidates:
            current_sel = -1
        elif current_sel < 0 or current_sel >= len(candidates):
            current_sel = 0

        if self._candidates_equal(cached, candidates):
            # 不变, 仅同步选中
            if 0 <= current_sel < len(candidates):
                btns = group.buttons()
                if current_sel < len(btns):
                    btns[current_sel].setChecked(True)
            return

        # 重建
        for btn in group.buttons():
            group.removeButton(btn)
        for i in reversed(range(container.count())):
            item = container.takeAt(i)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not candidates:
            placeholder = QLabel("(无输入或输入不足)")
            placeholder.setStyleSheet("color: #888;")
            container.addWidget(placeholder)
            return

        for idx, cand in enumerate(candidates):
            if kind == "purple":
                a = cand["purple_total_grids"]
                b = cand["purple_count"]
            else:
                a = cand["gold_total_grids"]
                b = cand["gold_count"]
            label = f"总 {a:>3} 格 / 物品 {b:>3} 件"
            rb = QRadioButton(label)
            if idx == current_sel:
                rb.setChecked(True)
            group.addButton(rb, idx)
            container.addWidget(rb)

    @staticmethod
    def _candidates_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if x != y:
                return False
        return True

    def _update_value_range_label(self, value_range: dict[str, float] | None) -> None:
        if not value_range:
            self.lbl_value_range.setText("预估仓库价值: —")
            return
        vmin = value_range.get("min")
        vmax = value_range.get("max")
        vmed = value_range.get("median")
        if vmin is None or vmax is None:
            self.lbl_value_range.setText("预估仓库价值: —")
            return
        if abs(vmax - vmin) < 1:
            self.lbl_value_range.setText(f"预估仓库价值: {_fmt_money(vmed)}")
        else:
            self.lbl_value_range.setText(
                f"预估仓库价值: {_fmt_money(vmin)} ~ {_fmt_money(vmax)}    "
                f"(中位 {_fmt_money(vmed)})"
            )

    def _update_selected_detail(self) -> None:
        p_idx = self.purple_candidates_group.checkedId()
        g_idx = self.gold_candidates_group.checkedId()
        p_cand = (
            self._cached_purple_candidates[p_idx]
            if 0 <= p_idx < len(self._cached_purple_candidates)
            else None
        )
        g_cand = (
            self._cached_gold_candidates[g_idx]
            if 0 <= g_idx < len(self._cached_gold_candidates)
            else None
        )
        if p_cand is None:
            self.lbl_selected_detail.setText("当前选: 未确定紫色候选")
            return
        inputs = self._collect_inputs()
        est = self.current_strategy.compute_estimate(inputs, p_cand, g_cand)
        parts = [
            f"紫 {est['purple_grids']} 格",
        ]
        if est["split_mode"]:
            parts.append(f"金 {est['gold_grids']} 格")
            parts.append(f"红 {est['red_grids']} 格")
        else:
            parts.append(f"金红剩余 {est['gold_red_grids']} 格")
        if est["estimated_value"] is not None:
            parts.append(f"估值 {_fmt_money(est['estimated_value'])}")
        err = est.get("error")
        if err:
            parts.append(f"⚠ {err}")
        self.lbl_selected_detail.setText("当前选: " + "  |  ".join(parts))

    def _on_candidate_toggled(self, kind: str, btn_id: int, checked: bool) -> None:
        if not checked:
            return
        key = f"selected_{kind}_idx"
        self.current_record.setdefault("predicted", {})[key] = btn_id
        self._update_selected_detail()
        self._save_timer.start()

    def _check_consistency(self) -> None:
        # 不再需要 (紫/金/红 manual entry 已移除)
        return

    # ---------- 收集字段 → record dict ----------

    def _collect_inputs(self) -> dict[str, Any]:
        return {
            "T": self.in_T.value() or None,
            "B": self.in_B.value() or None,
            "WG": self.in_WG.value() or None,
            "purple_avg": self.in_purple_avg.value() or None,
            "purple_count_est": self.in_purple_count_est.value() or None,
            "purple_total_grids": self.in_purple_total_grids.value() or None,
            "purple_count": self.in_purple_count.value() or None,
            "purple_total_value": self.in_purple_total_value.value() or None,
            "gold_avg": self.in_gold_avg.value() or None,
            "gold_count_est": self.in_gold_count_est.value() or None,
            "gold_total_grids": self.in_gold_total_grids.value() or None,
            "gold_count": self.in_gold_count.value() or None,
            "gold_total_value": self.in_gold_total_value.value() or None,
            "v_wg": self.in_v_wg.value() or None,
            "v_b": self.in_v_b.value() or None,
            "v_p": self.in_v_p.value() or None,
            "v_jr": self.in_v_jr.value() or None,
            "v_g": self.in_v_g.value() or None,
            "v_r": self.in_v_r.value() or None,
            # OCR 自动截图填充时保存的游戏原图路径 (GUI 无对应控件, 从 record 透传)
            "screenshot_path": self.current_record.get("inputs", {}).get("screenshot_path"),
        }

    def _collect_predicted(self, result: dict[str, Any] | None = None) -> dict[str, Any]:
        p_idx = self.purple_candidates_group.checkedId()
        g_idx = self.gold_candidates_group.checkedId()
        if p_idx < 0:
            p_idx = 0
        if g_idx < 0:
            g_idx = 0
        result = result or {}
        return {
            "purple_candidates": result.get("purple_candidates", []),
            "gold_candidates": result.get("gold_candidates", []),
            "value_range": result.get("value_range"),
            "selected_purple_idx": p_idx,
            "selected_gold_idx": g_idx,
        }

    def _collect_actual(self) -> dict[str, Any]:
        return {
            "total_value": self.act_total_value.value() or None,
            "screenshot_path": self.screenshot_widget.get_path() or None,
        }

    # ---------- autosave ----------

    def _do_save(self) -> None:
        if self._loading:
            return
        rec = self.current_record

        # 更新所有字段到 record
        rec["strategy"] = self.current_strategy.name
        rec["session_id"] = self.current_session_id
        rec["map_id"] = self.map_combo.currentData() if self.map_combo.currentData() else _try_int(self.map_combo.currentText())
        rec["hero_id"] = self.hero_combo.currentData() if self.hero_combo.currentData() else _try_int(self.hero_combo.currentText())
        rec["inputs"] = self._collect_inputs()

        # 重算并存预测
        result = self.current_strategy.compute(rec["inputs"])
        rec["predicted"] = self._collect_predicted(result)

        bid = self.in_bid.value() or None
        rec["bid"] = bid
        # 自动状态升级 (只升不降): draft → bid_placed when bid > 0
        if rec.get("status") == "draft" and bid:
            rec["status"] = "bid_placed"

        rec["actual"] = self._collect_actual()
        rec["note"] = self.note_edit.toPlainText().strip()

        try:
            self.store.upsert(rec)
        except OSError as e:
            self.status_bar.showMessage(
                f"⚠ 保存失败 (文件可能被占用, 请关闭外部编辑器): {e}", 5000
            )
            return

        # 持久化价格默认值（每次价格改了就同步保存）
        self.config.set_strategy_defaults(
            self.current_strategy.name,
            {
                "v_wg": float(self.in_v_wg.value()),
                "v_b": float(self.in_v_b.value()),
                "v_p": float(self.in_v_p.value()),
                "v_jr": float(self.in_v_jr.value()),
                "v_g": float(self.in_v_g.value()),
                "v_r": float(self.in_v_r.value()),
                "purple_count_est": float(self.in_purple_count_est.value()),
                "gold_count_est": float(self.in_gold_count_est.value()),
            },
        )

        self._refresh_status_label()
        self._refresh_session_label()

    # ---------- 按钮 ----------

    def _on_strategy_changed(self, name: str) -> None:
        if name not in self.strategies:
            return
        self.current_strategy = self.strategies[name]
        self._on_field_changed()

    def _on_new_game(self) -> None:
        # 先保存当前
        self._do_save()
        # 让用户选英雄（可选）
        self._start_fresh_record(new_session=True)

    def _on_new_auction(self) -> None:
        # 先保存当前
        self._do_save()
        # 同 session 新一条
        self._start_fresh_record(new_session=False)

    def _on_open_history(self) -> None:
        # 在最后实现 (history_window) 后接进来；这里先延迟 import 避开循环
        from .history_window import HistoryWindow
        self._do_save()
        self._history = HistoryWindow(self.store, on_select=self._open_record_from_history, parent=self)
        self._history.show()

    def _open_record_from_history(self, record_id: str) -> None:
        rec = self.store.get(record_id)
        if not rec:
            return
        self._do_save()  # 先存当前
        self.current_record = rec
        self.current_session_id = rec.get("session_id") or new_session_id()
        self._load_record_into_ui(rec)

    # ---------- 从游戏截图自动填充 (OCR) ----------

    def _on_save_screenshot_toggled(self, checked: bool) -> None:
        """保存原图开关 toggled，持久化到 config.json。"""
        self.config.set(KEY_OCR_SAVE_SCREENSHOT, bool(checked))

    def _on_ocr_button_clicked(self) -> None:
        """从游戏截图按钮被点击。

        若已有 worker 在跑：标记"取消旧 worker 的结果 + 排队新一次"，
        等旧 worker 结束后自动启动新 worker。
        """
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            # 取消旧 worker 的结果回填，并排队一次新识别
            self._ocr_worker.cancel()
            self._ocr_discard_current = True
            self._ocr_pending = True
            self.status_bar.showMessage("已取消上一次识别，正在重新识别...", 3000)
            return
        self._start_ocr_worker()

    def _start_ocr_worker(self) -> None:
        self.btn_ocr.setText("识别中... (再点取消重试)")
        self._ocr_discard_current = False
        self._ocr_worker = OCRWorker(parent=self)
        self._ocr_worker.finished_ok.connect(self._on_ocr_finished_ok)
        self._ocr_worker.finished_err.connect(self._on_ocr_finished_err)
        self._ocr_worker.finished.connect(self._on_ocr_thread_finished)
        self._ocr_worker.start()

    def _on_ocr_thread_finished(self) -> None:
        """QThread.finished 信号：worker run() 已退出。"""
        # 恢复按钮文案（finished_ok / finished_err 也会改文案，这里兜底）
        self.btn_ocr.setText("📷 从游戏截图自动填充")
        # 若期间被点过一次，启动新一次
        if self._ocr_pending:
            self._ocr_pending = False
            self._start_ocr_worker()

    def _on_ocr_finished_ok(self, result: Any, img: Any) -> None:
        """OCR 成功回调。result: OCRResult, img: PIL.Image"""
        if self._ocr_discard_current:
            return
        self.btn_ocr.setText("📷 从游戏截图自动填充")

        # 收集已识别到的字段，按"增量填充"策略：识别到的覆盖输入框，未识别到的保留原值
        parts: list[str] = []
        if result.total_grids is not None:
            self.in_T.setValue(int(result.total_grids))
            parts.append(f"总格数={result.total_grids}")
        if result.blue_grids is not None:
            self.in_B.setValue(int(result.blue_grids))
            parts.append(f"蓝色格数={result.blue_grids}")
        if result.white_green_grids is not None:
            self.in_WG.setValue(int(result.white_green_grids))
            parts.append(f"白绿格数={result.white_green_grids}")
        if result.purple_avg is not None:
            self.in_purple_avg.setValue(float(result.purple_avg))
            parts.append(f"紫色平均占用格数={result.purple_avg}")

        if not parts:
            self.status_bar.showMessage(
                "未识别到拍卖面板数据，请确认游戏画面停留在拍卖详情页（中间面板可见）",
                6000,
            )
            return

        # 保存原图（如果开关打开）
        screenshot_msg = ""
        if self.cb_save_screenshot.isChecked():
            try:
                rel = self._save_input_screenshot(img)
                if rel:
                    self.current_record.setdefault("inputs", {})["screenshot_path"] = rel
                    screenshot_msg = "，原图已保存"
            except OSError as e:
                screenshot_msg = f"，原图保存失败: {e}"

        self.status_bar.showMessage(
            "已识别: " + ", ".join(parts) + screenshot_msg, 6000
        )
        # autosave (按钮 setValue 已经触发 _on_field_changed，再 schedule 一次保险)
        self._schedule_save()

    def _on_ocr_finished_err(self, msg: str) -> None:
        if self._ocr_discard_current:
            return
        self.btn_ocr.setText("📷 从游戏截图自动填充")
        self.status_bar.showMessage(msg, 6000)

    def _save_input_screenshot(self, img: Any) -> str | None:
        """把 PIL.Image 存到 screenshots/<record_id>-input.png，返回相对路径。"""
        rec_id = self.current_record.get("record_id")
        if not rec_id:
            return None
        target = PROJECT_DIR / "screenshots" / f"{rec_id}-input.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(target), "PNG")
        return target.relative_to(PROJECT_DIR).as_posix()

    def _on_mark_complete(self) -> None:
        self.current_record["status"] = "completed"
        self._do_save()
        self.status_bar.showMessage("已标记完成，可纳入 ML 数据集", 3000)

    def _on_revert_draft(self) -> None:
        self.current_record["status"] = "draft"
        self._do_save()
        self.status_bar.showMessage("已回滚到 draft", 2000)

    def _on_delete(self) -> None:
        rid = self.current_record.get("record_id")
        if not rid:
            return
        ans = QMessageBox.question(
            self,
            "删除本条记录",
            "确定删除当前记录吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(rid)
        self.status_bar.showMessage("已删除", 2000)
        self._start_fresh_record(new_session=False)


def _try_int(s: str) -> int | None:
    try:
        return int(s.strip().split()[0]) if s.strip() else None
    except (ValueError, IndexError):
        return None
