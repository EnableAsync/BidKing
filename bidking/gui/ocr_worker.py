"""QThread worker: 把截图 + OCR 跑在后台，避免冻 UI 线程。

支持"取消重试"语义: 调用 cancel() 后即使 run() 还在跑完，结果信号也不会再被发出
（实现上 run() 内部 RapidOCR 是 C++ 不可打断，所以是"丢结果"而非真正中止）。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..capture.window_capture import (
    CaptureFailed,
    GameWindowNotFound,
    capture_game_window,
)
from ..ocr.auction_ocr import extract_fields


class OCRWorker(QThread):
    """后台执行游戏截图 + OCR + 正则提取。

    Signals:
        finished_ok(OCRResult, PIL.Image): 成功时发，第二参数是原始游戏截图
        finished_err(str): 失败时发，附错误描述
    """

    finished_ok = Signal(object, object)  # (OCRResult, PIL.Image)
    finished_err = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消。已在跑的 run() 不会停下，但跑完不会发信号。"""
        self._cancelled = True

    def run(self) -> None:  # noqa: D401 — QThread API
        try:
            img = capture_game_window()
            if self._cancelled:
                return
            result = extract_fields(img)
            if self._cancelled:
                return
            self.finished_ok.emit(result, img)
        except GameWindowNotFound as e:
            if not self._cancelled:
                self.finished_err.emit(str(e))
        except CaptureFailed as e:
            if not self._cancelled:
                self.finished_err.emit(f"游戏画面截图失败：{e}")
        except Exception as e:  # 兜底，OCR 引擎可能抛任何东西
            if not self._cancelled:
                self.finished_err.emit(
                    f"截图识别过程中发生异常：{type(e).__name__}: {e}"
                )
