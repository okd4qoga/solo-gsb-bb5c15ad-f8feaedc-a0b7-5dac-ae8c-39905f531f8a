"""Tests for same-judge comparable comparison (read-only pure transform)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.same_judge_compare import (
    LEAD_A,
    LEAD_B,
    LEAD_TIE,
    PAIR_COMPARABLE,
    PAIR_INCOMPARABLE,
    SJ_A_MISSING,
    SJ_B_MISSING,
    SJ_COMPARABLE,
    SJ_NO_COMMON,
    same_judge_comparison,
)
from app.scoring import analyze


def make_records(rows):
    # rows: (contestant, judge, item, score)
    return [
        {"id": i + 1, "contestant": c, "judge": j, "item": it, "score": s}
        for i, (c, j, it, s) in enumerate(rows)
    ]


# 单一评委 J → 校准分 == 原始分（单评委分组 MAD=0 时 z=0，校准分=全局中位数）。
# 为保证最终分即原始分，相关用例统一对全部评分保留原分。
def raw_analysis(rows, scope="judge"):
    records = make_records(rows)
    decisions = {r["id"]: (True, "全部保留原分") for r in records}
    return analyze(records, decisions, scope)


def by_pair(result):
    return {(p["a"], p["b"]): p for p in result["pairs"]}


def by_item(pair):
    return {r["item"]: r for r in pair["items"]}


class TestSameJudgeBasics(unittest.TestCase):
    def setUp(self):
        # I1: A/B 共同评委 JA、JB；JC 只评 B（单侧评委不进共同集合）。
        # I2: A/B 共同评委 JA（JB 只评 A）。
        # I3: 只有 A 有评分（B 缺项目）。
        self.rows = [
            ("A", "JA", "I1", 8.0), ("A", "JB", "I1", 6.0),
            ("B", "JA", "I1", 5.0), ("B", "JB", "I1", 7.0), ("B", "JC", "I1", 10.0),
            ("A", "JA", "I2", 9.0), ("A", "JB", "I2", 3.0),
            ("B", "JA", "I2", 4.0),
            ("A", "JA", "I3", 5.0),
        ]
        self.a = raw_analysis(self.rows)
        self.r = same_judge_comparison(self.a)

    def test_summary_counts(self):
        s = self.r["summary"]
        self.assertEqual(s["contestants"], 2)
        self.assertEqual(s["pairs_total"], 1)
        self.assertEqual(s["pairs_comparable"], 1)
        self.assertEqual(s["pairs_incomparable"], 0)
        self.assertEqual(s["comparable_items"], 2)
        self.assertEqual(s["incomparable_items"], 1)
        self.assertEqual(s["incomparable_reasons"][SJ_B_MISSING], 1)

    def test_common_judge_average_uses_same_set(self):
        p = self.r["pairs"][0]
        items = by_item(p)
        i1 = items["I1"]
        self.assertEqual(i1["status"], SJ_COMPARABLE)
        self.assertEqual(i1["common_judges"] and [j["judge"] for j in i1["common_judges"]], ["JA", "JB"])
        self.assertEqual(i1["judges_only_b"], ["JC"])
        # A 共同评委均分 = (8+6)/2 = 7；B = (5+7)/2 = 6（JC 的 10 分不得计入）。
        self.assertAlmostEqual(i1["avg_a"], 7.0)
        self.assertAlmostEqual(i1["avg_b"], 6.0)
        self.assertAlmostEqual(i1["delta"], 1.0)
        self.assertEqual(i1["lead"], LEAD_A)
        i2 = items["I2"]
        self.assertEqual([j["judge"] for j in i2["common_judges"]], ["JA"])
        self.assertEqual(i2["judges_only_a"], ["JB"])
        # 共同集合只有 JA：A=9、B=4；A 独有的 JB=3 不得拉低 A 的共同均分。
        self.assertAlmostEqual(i2["avg_a"], 9.0)
        self.assertAlmostEqual(i2["avg_b"], 4.0)
        self.assertAlmostEqual(i2["delta"], 5.0)

    def test_pair_aggregation_and_full_totals(self):
        p = self.r["pairs"][0]
        # A 全量总分 18 居前，有序对即 (A, B)。
        self.assertEqual((p["a"], p["b"]), ("A", "B"))
        # 同评委差值合计 = 1 + 5 = 6。
        self.assertAlmostEqual(p["item_delta_sum"], 6.0)
        self.assertEqual(p["lead"], LEAD_A)
        self.assertEqual(p["lead_counts"], {"a_leads": 2, "b_leads": 0, "ties": 0})
        # 全量最终总分：A = 7(I1均) + 6(I2均) + 5(I3) = 18；
        # B = (5+7+10)/3 ≈ 7.333(I1均) + 4(I2均) = 11.333。
        self.assertAlmostEqual(p["full_total_a"], 18.0)
        self.assertAlmostEqual(p["full_total_b"], (5 + 7 + 10) / 3 + 4)
        self.assertEqual(p["full_lead"], LEAD_A)
        self.assertFalse(p["reversed"])

    def test_missing_item_status(self):
        i3 = by_item(self.r["pairs"][0])["I3"]
        self.assertEqual(i3["status"], SJ_B_MISSING)
        self.assertIsNone(i3["avg_a"])
        self.assertIsNone(i3["avg_b"])
        self.assertIsNone(i3["delta"])
        self.assertEqual(i3["judge_count_a"], 1)
        self.assertEqual(i3["judge_count_b"], 0)


class TestNoCommonJudgeAndIncomparablePair(unittest.TestCase):
    def test_disjoint_judges_is_incomparable_item_not_zero(self):
        rows = [
            ("A", "JA", "I1", 2.0), ("A", "JB", "I2", 9.0),
            ("B", "JB", "I1", 9.0), ("B", "JA", "I2", 2.0),
        ]
        r = same_judge_comparison(raw_analysis(rows))
        p = r["pairs"][0]
        items = by_item(p)
        self.assertEqual(items["I1"]["status"], SJ_NO_COMMON)
        self.assertEqual(items["I2"]["status"], SJ_NO_COMMON)
        self.assertEqual(items["I1"]["common_count"], 0)
        self.assertIsNone(items["I1"]["delta"])
        # 没有任何可比项目 → 整对不可比，不给同评委领先者，也不按 0 分判成平局。
        self.assertEqual(p["status"], PAIR_INCOMPARABLE)
        self.assertFalse(p["comparable"])
        self.assertIsNone(p["item_delta_sum"])
        self.assertIsNone(p["lead"])
        self.assertEqual(p["comparable_item_count"], 0)
        self.assertEqual(p["incomparable_item_count"], 2)
        self.assertEqual(r["summary"]["pairs_incomparable"], 1)
        self.assertEqual(r["summary"]["incomparable_reasons"][SJ_NO_COMMON], 2)
        # 全量总分仍正常给出（A=B=11，并列），但不可比对不参与反向判定。
        self.assertEqual(p["full_lead"], LEAD_TIE)
        self.assertFalse(p["reversed"])

    def test_one_missing_one_no_common_still_comparable_pair(self):
        # I1 共同评委 JA 可比；I2 双方评委不相交；I3 A 缺项目。
        rows = [
            ("A", "JA", "I1", 8.0), ("B", "JA", "I1", 5.0),
            ("A", "JB", "I2", 9.0), ("B", "JC", "I2", 1.0),
            ("B", "JA", "I3", 7.0),
        ]
        p = same_judge_comparison(raw_analysis(rows))["pairs"][0]
        items = by_item(p)
        self.assertTrue(p["comparable"])
        self.assertEqual(p["comparable_item_count"], 1)
        self.assertEqual(p["incomparable_item_count"], 2)
        self.assertAlmostEqual(p["item_delta_sum"], 3.0)  # 仅 I1 计入
        self.assertEqual(items["I2"]["status"], SJ_NO_COMMON)
        self.assertEqual(items["I3"]["status"], SJ_A_MISSING)


class TestReversal(unittest.TestCase):
    def test_reversed_pair_flag(self):
        # 共同评委 JA、JB：A 共同均分 9，B 共同均分 8 → 同评委口径 A 领先 1。
        # I2 只有 B 参评（A 缺项目，不计 0 分）：B 全量总分额外 +9 实现全量反超。
        rows = [
            ("A", "JA", "I1", 9.0), ("A", "JB", "I1", 9.0),
            ("B", "JA", "I1", 8.0), ("B", "JB", "I1", 8.0),
            ("B", "JA", "I2", 9.0),
        ]
        r = same_judge_comparison(raw_analysis(rows))
        # 选手按全量最终名次进入有序对：B(17) 在前、A(9) 在后，即有序对为 (B, A)。
        p = r["pairs"][0]
        self.assertEqual((p["a"], p["b"]), ("B", "A"))
        self.assertTrue(p["comparable"])
        # 有序对 a=B：共同均分差 8−9 = −1；按名字看 A 才是同评委口径领先者。
        self.assertAlmostEqual(p["item_delta_sum"], -1.0)
        self.assertEqual(p["lead"], LEAD_B)  # 有序对的 B 即选手 A
        # 全量：选手 A=9；选手 B=8+9=17 → 全量选手 B 领先，与同评委口径相反。
        self.assertAlmostEqual(p["full_total_a"], 17.0)
        self.assertAlmostEqual(p["full_total_b"], 9.0)
        self.assertEqual(p["full_lead"], LEAD_A)  # 有序对的 A 即选手 B
        self.assertTrue(p["reversed"])
        self.assertEqual(r["summary"]["pairs_reversed"], 1)

    def test_same_direction_not_reversed(self):
        rows = [
            ("A", "JA", "I1", 9.0), ("A", "JB", "I1", 9.0),
            ("B", "JA", "I1", 5.0), ("B", "JB", "I1", 5.0),
            ("A", "JA", "I2", 8.0), ("B", "JA", "I2", 4.0),
        ]
        p = same_judge_comparison(raw_analysis(rows))["pairs"][0]
        self.assertEqual(p["lead"], LEAD_A)
        self.assertEqual(p["full_lead"], LEAD_A)
        self.assertFalse(p["reversed"])

    def test_full_tie_is_never_reversed(self):
        # 全量总分并列时即使同评委口径分出方向，也不标反向。
        rows = [
            ("A", "JA", "I1", 9.0),
            ("B", "JA", "I1", 8.0), ("B", "JA", "I2", 1.0),
        ]
        p = same_judge_comparison(raw_analysis(rows))["pairs"][0]
        self.assertEqual(p["full_lead"], LEAD_TIE)
        self.assertEqual(p["lead"], LEAD_A)
        self.assertFalse(p["reversed"])


class TestCommonJudgeWeightsAndDetail(unittest.TestCase):
    def test_average_is_unweighted_over_common_judges(self):
        # I1 共同评委 JA、JB、JC；A 另有 JD、B 另有 JE，均必须被剔除。
        rows = [
            ("A", "JA", "I1", 3.0), ("A", "JB", "I1", 6.0), ("A", "JC", "I1", 9.0),
            ("A", "JD", "I1", 100.0),
            ("B", "JA", "I1", 4.0), ("B", "JB", "I1", 5.0), ("B", "JC", "I1", 8.0),
            ("B", "JE", "I1", 0.0),
        ]
        p = same_judge_comparison(raw_analysis(rows))["pairs"][0]
        i1 = by_item(p)["I1"]
        self.assertEqual([j["judge"] for j in i1["common_judges"]], ["JA", "JB", "JC"])
        self.assertEqual(i1["judges_only_a"], ["JD"])
        self.assertEqual(i1["judges_only_b"], ["JE"])
        self.assertAlmostEqual(i1["avg_a"], 6.0)
        self.assertAlmostEqual(i1["avg_b"], (4 + 5 + 8) / 3)
        # 明细逐评委给出双方最终分。
        ja = next(j for j in i1["common_judges"] if j["judge"] == "JA")
        self.assertEqual(ja, {"judge": "JA", "score_a": 3.0, "score_b": 4.0})

    def test_exact_tie(self):
        rows = [
            ("A", "JA", "I1", 7.0), ("B", "JA", "I1", 7.0),
            ("A", "JB", "I1", 8.0), ("B", "JB", "I1", 8.0),
        ]
        p = same_judge_comparison(raw_analysis(rows))["pairs"][0]
        self.assertEqual(p["lead"], LEAD_TIE)
        self.assertEqual(p["full_lead"], LEAD_TIE)
        self.assertEqual(p["lead_counts"]["ties"], 1)
        self.assertAlmostEqual(p["item_delta_sum"], 0.0)

    def test_pair_deltas_are_signed_a_minus_b(self):
        # A 的共同评委均分 (8+6)/2=7，B=5 → 有序对 (A,B) 差值合计为 +2
        # （注意是「同一共同评委集合的均分」之差，不是逐评委分差之和）。
        rows_a_leads = [
            ("A", "JA", "I1", 8.0), ("B", "JA", "I1", 5.0),
            ("A", "JB", "I1", 6.0), ("B", "JB", "I1", 5.0),
        ]
        rows_b_leads = [
            ("A", "JA", "I1", 5.0), ("B", "JA", "I1", 8.0),
            ("A", "JB", "I1", 5.0), ("B", "JB", "I1", 6.0),
        ]
        p1 = same_judge_comparison(raw_analysis(rows_a_leads))["pairs"][0]
        p2 = same_judge_comparison(raw_analysis(rows_b_leads))["pairs"][0]
        self.assertEqual((p1["a"], p1["b"]), ("A", "B"))
        self.assertAlmostEqual(p1["item_delta_sum"], 2.0)
        self.assertEqual(p1["lead"], LEAD_A)
        # 镜像数据下 B 全量与同评委口径均领先，有序对翻转为 (B,A)：
        # 差值始终为「有序对 a − b」= 7 − 5 = +2，领先者为有序对的 a（选手 B）。
        self.assertEqual((p2["a"], p2["b"]), ("B", "A"))
        self.assertAlmostEqual(p2["item_delta_sum"], 2.0)
        self.assertEqual(p2["lead"], LEAD_A)


class TestScopeAndDecisions(unittest.TestCase):
    def test_reflects_decisions_and_scope(self):
        # A=8、B=6、C=6：按评委分组下 A 的 8 是组内唯一高值，稳健标准化把它
        # 压回组中位 6（calibrated == 6），默认采用校准分时 A、B 项目均分相等。
        rows = [
            ("A", "JA", "I1", 8.0), ("B", "JA", "I1", 6.0), ("C", "JA", "I1", 6.0),
            ("A", "JB", "I1", 8.0), ("B", "JB", "I1", 6.0), ("C", "JB", "I1", 6.0),
        ]
        records = make_records(rows)
        a0 = analyze(records, {}, "judge")
        r0 = same_judge_comparison(a0)
        self.assertEqual(r0["scope"], "judge")
        # 默认采用校准分：A 的高分被压回中位数，共同评委均分差为 0。
        self.assertAlmostEqual(by_pair(r0)[("A", "B")]["item_delta_sum"], 0.0)
        # 只对 A 的评分保留原分并切到按评委×项目：同评委口径 A 重新领先 2。
        decisions = {r["id"]: (True, "A 保留原分") for r in records if r["contestant"] == "A"}
        a1 = analyze(records, decisions, "judge_item")
        r1 = same_judge_comparison(a1)
        self.assertEqual(r1["scope"], "judge_item")
        self.assertAlmostEqual(by_pair(r1)[("A", "B")]["item_delta_sum"], 2.0)

    def test_empty_analysis(self):
        r = same_judge_comparison(analyze([], {}, "judge"))
        self.assertEqual(r["pairs"], [])
        self.assertEqual(r["summary"]["pairs_total"], 0)
        self.assertEqual(r["summary"]["full_leads"], {"a_leads": 0, "b_leads": 0, "ties": 0})

    def test_single_contestant_has_no_pairs(self):
        rows = [("A", "JA", "I1", 8.0), ("A", "JB", "I1", 9.0)]
        r = same_judge_comparison(raw_analysis(rows))
        self.assertEqual(r["pairs"], [])
        self.assertEqual(r["summary"]["contestants"], 1)

    def test_item_scored_by_neither_is_omitted(self):
        rows = [
            ("A", "JA", "I1", 8.0), ("B", "JA", "I1", 5.0),
            ("C", "JA", "I9", 7.0),
        ]
        p = by_pair(same_judge_comparison(raw_analysis(rows)))[("A", "B")]
        # I9 双方都没评 → 不出现在 A/B 的项目明细里。
        self.assertEqual([r["item"] for r in p["items"]], ["I1"])
        # A/C、B/C 的 I9 与 I1 互缺：不可比对。
        r = same_judge_comparison(raw_analysis(rows))
        for p in r["pairs"]:
            if p["a"] in ("A", "B") and p["b"] == "C":
                self.assertFalse(p["comparable"])


if __name__ == "__main__":
    unittest.main()
