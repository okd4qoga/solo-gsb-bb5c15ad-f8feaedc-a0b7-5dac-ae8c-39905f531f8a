"""同评委可比对照（只读纯函数）。

在当前校准范围与逐项裁决形成的**最终分**（每条评分的 ``effective``）基础上，
对每一对选手、**逐项目**只取双方在该项目上**共同评分的评委**（共同评委集合
随项目而变）：

- 双方在该项目都有评分且共同评委非空 → 该项目**可比**：分别对**同一共同评委
  集合**上的最终分求均值，``差值 = A 的共同评委均分 − B 的共同评委均分``；
- 一方在该项目没有任何评分 → 该项目不可比（``a_missing`` / ``b_missing``），
  **不按 0 分处理**；
- 双方都有评分但评委集合完全不相交 → 该项目不可比（``no_common_judge``），
  同样不按 0 分处理；
- 双方都没有评分的项目不出现在结果中（与任何一方都无关）。

一对选手的**同评委对照差值** = 各可比项目差值之和（不可比项目一律不参与），
据此给出该对照口径下的领先者；没有任何可比项目的选手对整体标为**不可比**。

再与现有**全量最终总分**（各自全部参评项目的项目最终均分之和）的方向对照：
全量领先者与同评委对照领先者都非同分且方向相反时，标记该选手对为**反向**。

整个计算只读，不触碰评分、裁决或会话；评分、裁决或校准范围变化后由调用方
重新计算。
"""
from __future__ import annotations

from typing import Any, Mapping

from .scoring import SCOPE_LABELS, TIE_EPS

SJ_COMPARABLE = "comparable"
SJ_A_MISSING = "a_missing"
SJ_B_MISSING = "b_missing"
SJ_NO_COMMON = "no_common_judge"
SJ_ITEM_STATUSES = (SJ_COMPARABLE, SJ_A_MISSING, SJ_B_MISSING, SJ_NO_COMMON)
SJ_ITEM_STATUS_LABELS = {
    SJ_COMPARABLE: "可比",
    SJ_A_MISSING: "A 缺该项目",
    SJ_B_MISSING: "B 缺该项目",
    SJ_NO_COMMON: "双方无共同评委",
}

PAIR_COMPARABLE = "comparable"
PAIR_INCOMPARABLE = "incomparable"
PAIR_STATUS_LABELS = {
    PAIR_COMPARABLE: "可比",
    PAIR_INCOMPARABLE: "不可比",
}

LEAD_A = "a"
LEAD_B = "b"
LEAD_TIE = "tie"
LEAD_LABELS = {LEAD_A: "A 领先", LEAD_B: "B 领先", LEAD_TIE: "持平"}


def _lead(delta: float) -> str:
    """Direction of an A−B delta with the same tie tolerance as the main rank."""
    if delta > TIE_EPS:
        return LEAD_A
    if delta < -TIE_EPS:
        return LEAD_B
    return LEAD_TIE


def _empty_lead_counts() -> dict[str, int]:
    return {"a_leads": 0, "b_leads": 0, "ties": 0}


def same_judge_comparison(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """对一次 ``analyze`` 结果做全体选手对的同评委可比对照（纯函数，只读）。

    只使用 ``analysis`` 中的 ``scope``、``judges``、``items`` 以及每位选手
    块的 ``eff_total`` / ``eff_rank`` 与逐项目评分行（每行的 ``judge`` 与
    ``effective`` 最终分）。
    """
    items = list(analysis["items"])
    judges = list(analysis["judges"])
    # analyze 已按最终名次排序；有序对 (block_a, block_b) 中 a 的名次不劣于 b。
    blocks_in_rank_order = list(analysis["contestants"])

    summary = {
        "contestants": len(blocks_in_rank_order),
        "pairs_total": 0,
        "pairs_comparable": 0,
        "pairs_incomparable": 0,
        "pairs_reversed": 0,
        "full_leads": _empty_lead_counts(),
        "comparable_leads": _empty_lead_counts(),
        "comparable_items": 0,
        "incomparable_items": 0,
        "incomparable_reasons": {
            SJ_A_MISSING: 0,
            SJ_B_MISSING: 0,
            SJ_NO_COMMON: 0,
        },
    }

    pairs: list[dict[str, Any]] = []
    pair_index = 0
    for ia, block_a in enumerate(blocks_in_rank_order):
        map_a = {ib["item"]: ib for ib in block_a["items"]}
        for block_b in blocks_in_rank_order[ia + 1:]:
            map_b = {ib["item"]: ib for ib in block_b["items"]}

            item_rows: list[dict[str, Any]] = []
            lead_counts = _empty_lead_counts()
            reason_counts = {
                SJ_A_MISSING: 0,
                SJ_B_MISSING: 0,
                SJ_NO_COMMON: 0,
            }
            delta_sum = 0.0

            for item in items:
                ib_a = map_a.get(item)
                ib_b = map_b.get(item)
                if ib_a is None and ib_b is None:
                    continue  # 双方都没有该项目，与本选手对无关。

                scores_a = (
                    {s["judge"]: s for s in ib_a["scores"]} if ib_a is not None else {}
                )
                scores_b = (
                    {s["judge"]: s for s in ib_b["scores"]} if ib_b is not None else {}
                )
                # 共同评委 / 单侧评委均按会话中评委首次出现的顺序输出。
                common = [j for j in judges if j in scores_a and j in scores_b]
                only_a = [j for j in judges if j in scores_a and j not in scores_b]
                only_b = [j for j in judges if j in scores_b and j not in scores_a]

                row: dict[str, Any] = {
                    "item": item,
                    "judge_count_a": len(scores_a),
                    "judge_count_b": len(scores_b),
                    "common_count": len(common),
                    "common_judges": [],
                    "judges_only_a": only_a,
                    "judges_only_b": only_b,
                    "avg_a": None,
                    "avg_b": None,
                    "delta": None,
                    "lead": None,
                }

                if ib_a is None:
                    row["status"] = SJ_A_MISSING
                    reason_counts[SJ_A_MISSING] += 1
                elif ib_b is None:
                    row["status"] = SJ_B_MISSING
                    reason_counts[SJ_B_MISSING] += 1
                elif not common:
                    row["status"] = SJ_NO_COMMON
                    reason_counts[SJ_NO_COMMON] += 1
                else:
                    vals_a = [float(scores_a[j]["effective"]) for j in common]
                    vals_b = [float(scores_b[j]["effective"]) for j in common]
                    avg_a = sum(vals_a) / len(vals_a)
                    avg_b = sum(vals_b) / len(vals_b)
                    delta = avg_a - avg_b
                    lead = _lead(delta)
                    row.update(
                        status=SJ_COMPARABLE,
                        avg_a=avg_a,
                        avg_b=avg_b,
                        delta=delta,
                        lead=lead,
                        common_judges=[
                            {
                                "judge": j,
                                "score_a": float(scores_a[j]["effective"]),
                                "score_b": float(scores_b[j]["effective"]),
                            }
                            for j in common
                        ],
                    )
                    delta_sum += delta
                    if lead == LEAD_A:
                        lead_counts["a_leads"] += 1
                    elif lead == LEAD_B:
                        lead_counts["b_leads"] += 1
                    else:
                        lead_counts["ties"] += 1

                item_rows.append(row)

            comparable_rows = [r for r in item_rows if r["status"] == SJ_COMPARABLE]
            incomparable_rows = [r for r in item_rows if r["status"] != SJ_COMPARABLE]
            comparable_count = len(comparable_rows)
            is_comparable = comparable_count > 0

            full_total_a = float(block_a["eff_total"])
            full_total_b = float(block_b["eff_total"])
            full_delta = full_total_a - full_total_b
            full_lead = _lead(full_delta)
            summary["full_leads"][
                "ties" if full_lead == LEAD_TIE else f"{full_lead}_leads"
            ] += 1

            if is_comparable:
                pair_lead = _lead(delta_sum)
                delta_sum_out: float | None = delta_sum
                lead_out: str | None = pair_lead
                pair_status = PAIR_COMPARABLE
                summary["pairs_comparable"] += 1
                summary["comparable_leads"][
                    "ties" if pair_lead == LEAD_TIE else f"{pair_lead}_leads"
                ] += 1
                # 反向：两种口径都能分出方向、且方向相反。
                reversed_pair = (
                    full_lead in (LEAD_A, LEAD_B)
                    and pair_lead in (LEAD_A, LEAD_B)
                    and full_lead != pair_lead
                )
                if reversed_pair:
                    summary["pairs_reversed"] += 1
            else:
                pair_lead = None
                delta_sum_out = None
                lead_out = None
                pair_status = PAIR_INCOMPARABLE
                reversed_pair = False
                summary["pairs_incomparable"] += 1

            summary["comparable_items"] += comparable_count
            summary["incomparable_items"] += len(incomparable_rows)
            for key in (SJ_A_MISSING, SJ_B_MISSING, SJ_NO_COMMON):
                summary["incomparable_reasons"][key] += reason_counts[key]

            pairs.append(
                {
                    "pair_index": pair_index,
                    "a": block_a["contestant"],
                    "b": block_b["contestant"],
                    "rank_a": block_a["eff_rank"],
                    "rank_b": block_b["eff_rank"],
                    "full_total_a": full_total_a,
                    "full_total_b": full_total_b,
                    "full_delta": full_delta,
                    "full_lead": full_lead,
                    "status": pair_status,
                    "comparable": is_comparable,
                    "comparable_item_count": comparable_count,
                    "incomparable_item_count": len(incomparable_rows),
                    "item_delta_sum": delta_sum_out,
                    "lead": lead_out,
                    "lead_counts": lead_counts,
                    "reversed": reversed_pair,
                    "incomparable_reasons": reason_counts,
                    "items": item_rows,
                }
            )
            pair_index += 1

    summary["pairs_total"] = len(pairs)
    scope = analysis.get("scope", "")
    return {
        "scope": scope,
        "scope_label": analysis.get("scope_label") or SCOPE_LABELS.get(scope, scope),
        "tie_eps": TIE_EPS,
        "item_status_labels": dict(SJ_ITEM_STATUS_LABELS),
        "pair_status_labels": dict(PAIR_STATUS_LABELS),
        "lead_labels": dict(LEAD_LABELS),
        "summary": summary,
        "pairs": pairs,
    }
