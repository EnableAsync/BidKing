"""对游戏截图做 OCR，提取 4 个关键数值。

Pipeline:
1. PIL Image → numpy ndarray
2. RapidOCR → 文本行列表（懒加载单例，首次 ~1s）
3. 拼接文本 + 正则匹配 4 个字段

4 个字段对应的游戏道具:
- 总格数  T  ← 总仓储空间 (25000银):  "所有藏品总格数"
- 蓝色   B   ← 良品扫描   (1000银):  "所有蓝色品质藏品总占XX格"
- 白绿   WG  ← 普品扫描   (500银):   "所有白绿品质藏品总占XX格"
- 紫色平均 c_p ← 优品均格 (1000银):  "所有紫色品质藏品平均占X.XX格"
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


# 共享单例（首次创建会加载 ~30MB 的 ONNX 模型，耗时 ~1s）
_ocr_instance: Optional[RapidOCR] = None
_ocr_lock = threading.Lock()


def _get_ocr() -> RapidOCR:
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                _ocr_instance = RapidOCR()
    return _ocr_instance


@dataclass
class OCRResult:
    """OCR + 正则匹配的结果。"""

    total_grids: Optional[int] = None        # T   总格数
    blue_grids: Optional[int] = None         # B   蓝色格数
    white_green_grids: Optional[int] = None  # WG  白绿格数
    purple_avg: Optional[float] = None       # c_p 紫色平均占用格数
    raw_text: str = ""                       # 拼接的 OCR 全文，便于调试和保存

    @property
    def matched_count(self) -> int:
        return sum(
            1
            for v in (
                self.total_grids,
                self.blue_grids,
                self.white_green_grids,
                self.purple_avg,
            )
            if v is not None
        )


# 字段提取正则。OCR 误识别带来的容错放宽：
# - "[^0-9]{0,20}?"  色名和数字之间允许若干非数字字符（"总占位数为"、"占位为"、"总占" 都见过）
# - 4 类品质实际文案：
#     蓝色:  "所有蓝色品质藏品总占位数为XX格"
#     白绿:  "所有白色和绿色品质藏品总占位数为XX格"  ← 注意是"白色和绿色"分写
#     紫色:  "所有紫色品质藏品平均占位约X.XX格"
#     总:    "所有藏品..."（没有色名）

# 总格数: 来自"总仓储空间"道具。文案首字符 "所有藏品" 后没有色名，可与色相区分。
_RE_TOTAL_PATTERNS = [
    re.compile(r"所有藏品[^0-9色]{0,25}?(\d{1,4})\s*格"),
    re.compile(r"总仓储[^0-9]{0,20}?(\d{1,4})\s*格"),
]

_RE_BLUE_PATTERNS = [
    re.compile(r"所有蓝色品质藏品[^0-9]{0,25}?(\d{1,4})\s*格"),
    re.compile(r"蓝色[^0-9]{0,25}?(\d{1,4})\s*格"),
]

_RE_WG_PATTERNS = [
    # 实际游戏文案: "所有白色和绿色品质藏品总占位数为XX格"
    re.compile(r"所有白色[和与及]?绿色品质藏品[^0-9]{0,25}?(\d{1,4})\s*格"),
    # 备选简写: "所有白绿品质藏品..."
    re.compile(r"所有白绿品质藏品[^0-9]{0,25}?(\d{1,4})\s*格"),
    # 宽松: 白色 + 绿色 同时出现
    re.compile(r"白色[和与及\s]*绿色[^0-9]{0,25}?(\d{1,4})\s*格"),
]

# 紫色"平均"占必须显式带"平均"，否则会撞上"紫色总占X格"(优品扫描出的是总格数)
_RE_PURPLE_AVG_PATTERNS = [
    re.compile(r"所有紫色品质藏品平均[^0-9]{0,10}?(\d+\.\d+)\s*格"),
    re.compile(r"紫色[^0-9]{0,20}?平均[^0-9]{0,10}?(\d+\.\d+)\s*格"),
]


def _normalize_text(text: str) -> str:
    """OCR 误识字符纠正。

    主要是字母 O/o 误识成数字 0，中文句号被识成小数点，等。
    """
    replacements = {
        "Ｏ": "0",   # 全角 O
        "．": ".",   # 全角点
        "。": ".",   # 中文句号 → 小数点
        "，": ",",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def _first_match(patterns: list[re.Pattern[str]], text: str) -> Optional[str]:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(1)
    return None


def extract_fields(img: Image.Image) -> OCRResult:
    """对截图做 OCR，再用正则提取 4 个字段。

    Args:
        img: PIL Image (RGB)
    Returns:
        OCRResult，4 个字段任一无匹配时对应字段为 None
    """
    ocr = _get_ocr()
    arr = np.asarray(img)
    raw_result, _ = ocr(arr)
    lines: list[str] = []
    if raw_result:
        for entry in raw_result:
            # entry 形如 [box, text, score]
            text = entry[1] if len(entry) > 1 else ""
            if text:
                lines.append(str(text))

    raw = "\n".join(lines)
    norm = _normalize_text(raw)

    out = OCRResult(raw_text=raw)

    s = _first_match(_RE_TOTAL_PATTERNS, norm)
    if s is not None:
        try:
            out.total_grids = int(s)
        except ValueError:
            pass

    s = _first_match(_RE_BLUE_PATTERNS, norm)
    if s is not None:
        try:
            out.blue_grids = int(s)
        except ValueError:
            pass

    s = _first_match(_RE_WG_PATTERNS, norm)
    if s is not None:
        try:
            out.white_green_grids = int(s)
        except ValueError:
            pass

    s = _first_match(_RE_PURPLE_AVG_PATTERNS, norm)
    if s is not None:
        try:
            out.purple_avg = float(s)
        except ValueError:
            pass

    return out
