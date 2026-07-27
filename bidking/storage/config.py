"""持久化默认值 (config.json)。

存放各策略的默认价格输入、上次使用的英雄、地图等。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# 已知的 config.json 顶层 key 常量（文档作用，便于查找）
KEY_OCR_SAVE_SCREENSHOT = "ocr_save_screenshot"  # bool, 是否保存 OCR 用的游戏原始截图


class Config:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get_strategy_defaults(self, strategy_name: str, fallback: dict[str, float]) -> dict[str, float]:
        stored = self._data.get("strategy_defaults", {}).get(strategy_name, {})
        merged = {**fallback, **stored}
        return merged

    def set_strategy_defaults(self, strategy_name: str, values: dict[str, float]) -> None:
        self._data.setdefault("strategy_defaults", {})[strategy_name] = values
        self._flush()

    # ---------- 价格预设 ----------

    def list_price_presets(self, strategy_name: str) -> list[str]:
        presets = self._data.get("price_presets", {}).get(strategy_name, {})
        return sorted(presets.keys())

    def get_price_preset(self, strategy_name: str, preset_name: str) -> dict[str, float] | None:
        preset = self._data.get("price_presets", {}).get(strategy_name, {}).get(preset_name)
        if preset is None:
            return None
        return dict(preset)

    def save_price_preset(
        self, strategy_name: str, preset_name: str, values: dict[str, float]
    ) -> None:
        bucket = self._data.setdefault("price_presets", {}).setdefault(strategy_name, {})
        bucket[preset_name] = dict(values)
        self._flush()

    def delete_price_preset(self, strategy_name: str, preset_name: str) -> bool:
        bucket = self._data.get("price_presets", {}).get(strategy_name, {})
        if preset_name in bucket:
            del bucket[preset_name]
            self._flush()
            return True
        return False

    def rename_price_preset(
        self, strategy_name: str, old_name: str, new_name: str
    ) -> bool:
        bucket = self._data.get("price_presets", {}).get(strategy_name, {})
        if old_name not in bucket or new_name in bucket:
            return False
        bucket[new_name] = bucket.pop(old_name)
        self._flush()
        return True

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._flush()
