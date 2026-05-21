"""数格子精算法。

通过任意组合的道具读数估算仓库价值范围。
对每个品质 (紫/金), 用户可以提供以下任一/任意组合 (全部可选):
  - 总格数 a  (扫描类道具: 优品扫描 / 极品扫描)
  - 物品数 b  (存量类道具: 优品存量 / 极品存量)
  - 平均格数 c (均格类道具: 优品均格 / 极品均格)
  - 物品数预估 b_est (主观判断, 当 b 缺失时配合 c 用)

输入越多, 反推的 (总格数, 物品数) 候选越少, 估值范围越窄。

输入字段:
  T:                 总格数
  B:                 蓝总格数
  WG:                白绿总格数
  purple_total_grids:   紫色总格数 a_p (可选)
  purple_count:         紫色物品数 b_p (可选)
  purple_avg:           紫色平均格数 c_p (可选, 2 位小数)
  purple_count_est:     紫色物品数预估 b_p_est (主观)
  gold_total_grids:     金色总格数 a_g (可选)
  gold_count:           金色物品数 b_g (可选)
  gold_avg:             金色平均格数 c_g (可选, 2 位小数)
  gold_count_est:       金色物品数预估 b_g_est (主观)

价格:
  v_wg, v_b, v_p:    白绿/蓝/紫 单格估价
  v_jr:              金红混合单格估价 (当未启用金色反推时用)
  v_g, v_r:          金/红 单格估价 (当启用金色反推时用)

输出:
  purple_candidates: list[{purple_total_grids, purple_count}]
  gold_candidates:   list[{gold_total_grids, gold_count}]  (空 = 未启用)
  value_min/max/median: float (跨所有候选组合)
  errors, warnings:  list[str]
"""
from __future__ import annotations

from typing import Any

from .base import StrategyBase


def find_candidates(c: float, max_items: int = 80) -> list[tuple[int, int]]:
    """反推满足 c <= a/b <= c+0.01 的所有 (a, b) 正整数对。"""
    if c <= 0:
        return []
    results: list[tuple[int, int]] = []
    upper = c + 0.01
    for b in range(1, max_items + 1):
        min_a = int(c * b)
        max_a = int(upper * b) + 2
        for a in range(min_a, max_a):
            if a <= 0:
                continue
            avg = a / b
            if c <= round(avg, 4) <= upper:
                results.append((a, b))
    return results


find_purple_candidates = find_candidates  # 旧名兼容


def pick_nearest_candidates(
    candidates: list[tuple[int, int]], b_est: int, k: int = 6,
    ensure_b: list[int] | None = None,
) -> list[tuple[int, int]]:
    """Pick k candidates nearest to b_est, with diversity anchors.

    When ensure_b is provided, candidates matching those b values are
    guaranteed a slot (up to one per anchor value), then remaining slots
    are filled from the nearest-to-b_est pool.
    """
    if not candidates:
        return []
    if ensure_b is None:
        return sorted(candidates, key=lambda ab: (abs(ab[1] - b_est), ab[1]))[:k]

    result: list[tuple[int, int]] = []
    remaining = list(candidates)

    # Pick one best match per anchor b (closest to b_est among that b value)
    for anchor in ensure_b:
        if len(result) >= k:
            break
        matches = [(a, b) for (a, b) in remaining if b == anchor]
        if matches:
            best = min(matches, key=lambda ab: abs(ab[1] - b_est))
            result.append(best)
            remaining.remove(best)

    # Fill rest with nearest to b_est
    by_est = sorted(remaining, key=lambda ab: (abs(ab[1] - b_est), ab[1]))
    for c in by_est:
        if len(result) >= k:
            break
        result.append(c)

    return result


def _determine_candidates(
    a: int | None,
    b: int | None,
    c: float | None,
    b_est: int | None,
    grid_key: str,
    count_key: str,
    k: int = 6,
    ensure_b: list[int] | None = None,
) -> list[dict[str, int]]:
    """根据某品质的多个可选输入, 返回该品质的 (总格数, 物品数) 候选 list。

    grid_key/count_key: 输出 dict 的键名 (purple_total_grids / gold_total_grids 等)。
    """
    if a is not None:
        if b is not None:
            return [{grid_key: a, count_key: b}]
        if c is not None and c > 0:
            b_derived = max(1, round(a / c))
            return [{grid_key: a, count_key: b_derived}]
        return [{grid_key: a, count_key: b_est or 1}]

    if c is not None and c > 0:
        all_c = find_candidates(c)
        if b is not None:
            matching = [(aa, bb) for (aa, bb) in all_c if bb == b]
            if matching:
                return [{grid_key: aa, count_key: bb} for (aa, bb) in matching]
            return []
        picked = pick_nearest_candidates(all_c, b_est or 1, k=k, ensure_b=ensure_b)
        return [{grid_key: aa, count_key: bb} for (aa, bb) in picked]

    return []


class GridActuarial(StrategyBase):
    name = "数格子精算法"
    defaults = {
        "v_wg": 100.0,
        "v_b": 800.0,
        "v_p": 2000.0,
        "v_jr": 20000.0,
        "v_g": 10000.0,
        "v_r": 30000.0,
        "purple_count_est": 10.0,
        "gold_count_est": 5.0,
    }

    def compute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []

        T = _to_int(inputs.get("T"))
        B = _to_int(inputs.get("B"))
        WG = _to_int(inputs.get("WG"))
        a_p = _to_int(inputs.get("purple_total_grids"))
        b_p = _to_int(inputs.get("purple_count"))
        c_p = _to_float(inputs.get("purple_avg"))
        b_p_est = _to_int(inputs.get("purple_count_est"))
        a_g = _to_int(inputs.get("gold_total_grids"))
        b_g = _to_int(inputs.get("gold_count"))
        c_g = _to_float(inputs.get("gold_avg"))
        b_g_est = _to_int(inputs.get("gold_count_est"))

        if T is None or B is None or WG is None:
            return _empty_output(errors, warnings)

        if B + WG > T:
            errors.append(f"蓝({B}) + 白绿({WG}) = {B+WG} 已经超过总格数 {T}")

        for label, v in (("紫色平均", c_p), ("金色平均", c_g)):
            if v is not None and v > 0:
                if abs(round(v, 2) - v) > 1e-9:
                    warnings.append(f"{label} {v} 超过 2 位小数, 道具读数只有 2 位精度")

        purple_candidates = _determine_candidates(
            a_p, b_p, c_p, b_p_est,
            grid_key="purple_total_grids", count_key="purple_count",
            k=12, ensure_b=list(range(8, 21)),
        )
        gold_enabled = (
            a_g is not None or (c_g is not None and c_g > 0) or b_g is not None
        )
        gold_candidates: list[dict[str, int]] = []
        if gold_enabled:
            gold_candidates = _determine_candidates(
                a_g, b_g, c_g, b_g_est,
                grid_key="gold_total_grids", count_key="gold_count",
                k=5,
            )

        if not purple_candidates:
            if c_p is not None or a_p is not None or b_p is not None:
                errors.append("紫色输入不足以反推 (a, b)")

        # 候选按物品数量升序排序 (展示顺序与索引保持一致)
        purple_candidates.sort(key=lambda d: d["purple_count"])
        gold_candidates.sort(key=lambda d: d["gold_count"])

        # 计算所有候选组合的估值, 取 min/max/median
        values: list[float] = []
        if purple_candidates:
            g_iter: list[dict[str, int] | None] = list(gold_candidates) if gold_candidates else [None]
            for pc in purple_candidates:
                for gc in g_iter:
                    est = self.compute_estimate(inputs, pc, gc)
                    if est["estimated_value"] is not None and est.get("error") is None:
                        values.append(est["estimated_value"])

        value_range: dict[str, float] | None = None
        if values:
            srt = sorted(values)
            value_range = {
                "min": srt[0],
                "max": srt[-1],
                "median": srt[len(srt) // 2],
            }

        return {
            "purple_candidates": purple_candidates,
            "gold_candidates": gold_candidates,
            "gold_enabled": gold_enabled,
            "value_range": value_range,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def compute_estimate(
        inputs: dict[str, Any],
        purple_cand: dict[str, Any] | None,
        gold_cand: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """按选中的 (紫, 金) 候选计算单点估值。

        gold_cand=None → 金红混合 (v_jr); 否则 → 拆分 (v_g + v_r)。
        若用户提供 purple_total_value / gold_total_value (优品估价 / 极品估价 道具),
        则覆盖该品质的 a × v 估算。
        """
        T = _to_int(inputs.get("T")) or 0
        B = _to_int(inputs.get("B")) or 0
        WG = _to_int(inputs.get("WG")) or 0
        v_wg = _to_float(inputs.get("v_wg")) or 0.0
        v_b = _to_float(inputs.get("v_b")) or 0.0
        v_p = _to_float(inputs.get("v_p")) or 0.0
        v_jr = _to_float(inputs.get("v_jr")) or 0.0
        v_g = _to_float(inputs.get("v_g")) or 0.0
        v_r = _to_float(inputs.get("v_r")) or 0.0
        p_total_value = _to_float(inputs.get("purple_total_value"))
        g_total_value = _to_float(inputs.get("gold_total_value"))

        if purple_cand is None:
            return {
                "purple_grids": 0, "gold_grids": 0, "red_grids": 0,
                "gold_red_grids": 0, "estimated_value": None,
                "split_mode": gold_cand is not None,
                "error": "未选紫色候选",
            }

        a_p = purple_cand["purple_total_grids"]
        gold_red = T - B - WG - a_p

        purple_value = p_total_value if (p_total_value is not None and p_total_value > 0) else a_p * v_p

        if gold_cand is not None:
            a_g = gold_cand["gold_total_grids"]
            a_r = gold_red - a_g
            err = None
            if a_r < 0:
                err = f"红色格数 = {a_r} < 0, 紫+金已超额"
            gold_value = g_total_value if (g_total_value is not None and g_total_value > 0) else a_g * v_g
            value = WG * v_wg + B * v_b + purple_value + gold_value + max(a_r, 0) * v_r
            return {
                "purple_grids": a_p, "gold_grids": a_g, "red_grids": a_r,
                "gold_red_grids": gold_red, "estimated_value": value,
                "split_mode": True, "error": err,
            }
        else:
            err = None
            if gold_red < 0:
                err = f"金红剩余 = {gold_red} < 0, 紫色已超额"
            value = WG * v_wg + B * v_b + purple_value + max(gold_red, 0) * v_jr
            return {
                "purple_grids": a_p, "gold_grids": 0, "red_grids": 0,
                "gold_red_grids": gold_red, "estimated_value": value,
                "split_mode": False, "error": err,
            }


def _empty_output(errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "purple_candidates": [],
        "gold_candidates": [],
        "gold_enabled": False,
        "value_range": None,
        "errors": errors,
        "warnings": warnings,
    }


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
