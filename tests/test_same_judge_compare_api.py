"""API tests for the read-only same-judge comparable comparison endpoint."""
import os
import sys
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="calib_sj_test_")
os.environ["CALIB_DATA_DIR"] = _tmp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()
client = TestClient(app)
SID = "samejudge-session"
URL = f"/api/sessions/{SID}/same-judge-compare"

# I1：A/B 共同评委 JA、JB；JC 只评 B。
# I2：共同评委只有 JA（JB 只评 A）。
# I3：只有 B 参评（A 缺项目）—— 让全量方向与同评委口径相反：
#   同评委共同均分 A 高于 B，但 B 凭 I3 在全量总分上反超。
CSV_BODY = (
    "选手,评委,项目,分数\n"
    "A,JA,I1,9\nA,JB,I1,9\n"
    "B,JA,I1,8\nB,JB,I1,8\nB,JC,I1,10\n"
    "A,JA,I2,9\nA,JB,I2,9\n"
    "B,JA,I2,8\n"
    "B,JA,I3,20\n"
)


def pairs_by_names(data):
    return {(p["a"], p["b"]): p for p in data["pairs"]}


class TestSameJudgeAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client.delete(f"/api/sessions/{SID}")
        r = client.post(
            f"/api/sessions/{SID}/upload?scope=judge",
            files={"file": ("sj.csv", CSV_BODY.encode("utf-8-sig"), "text/csv")},
        )
        assert r.status_code == 200, r.text
        # 全部保留原分，使同评委均分等于原分均值，断言可手算。
        analysis = client.get(f"/api/sessions/{SID}/analysis").json()
        for block in analysis["contestants"]:
            for ib in block["items"]:
                for s in ib["scores"]:
                    pr = client.patch(
                        f"/api/sessions/{SID}/scores/{s['id']}/decision",
                        json={"use_raw": True, "reason": "同评委对照测试"},
                    )
                    assert pr.status_code == 200, pr.text

    def test_01_pair_structure_and_common_judge_set(self):
        r = client.get(URL)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["scope"], "judge")
        self.assertIn("scope_label", data)
        self.assertEqual(data["item_status_labels"]["comparable"], "可比")
        self.assertEqual(data["pair_status_labels"]["incomparable"], "不可比")
        # 只有 A、B 两名选手 → 一对；B 全量总分高，有序对为 (B, A)。
        self.assertEqual(data["summary"]["pairs_total"], 1)
        p = data["pairs"][0]
        self.assertEqual((p["a"], p["b"]), ("B", "A"))
        items = {row["item"]: row for row in p["items"]}
        i1 = items["I1"]
        self.assertEqual(i1["status"], "comparable")
        self.assertEqual(
            [j["judge"] for j in i1["common_judges"]], ["JA", "JB"]
        )
        # 有序对 a=B、b=A：JC 仅评 B（a 侧），不得进入共同集合。
        # B 共同均分 = (8+8)/2 = 8；A = 9。
        self.assertAlmostEqual(i1["avg_a"], 8.0)
        self.assertAlmostEqual(i1["avg_b"], 9.0)
        self.assertEqual(i1["judges_only_a"], ["JC"])
        i2 = items["I2"]
        self.assertEqual([j["judge"] for j in i2["common_judges"]], ["JA"])
        self.assertEqual(i2["judges_only_b"], ["JB"])
        self.assertAlmostEqual(i2["avg_a"], 8.0)
        self.assertAlmostEqual(i2["avg_b"], 9.0)
        # I3 只有 B 参评：有序对 a=B 有、b=A 缺 → b_missing。
        i3 = items["I3"]
        self.assertEqual(i3["status"], "b_missing")
        self.assertIsNone(i3["delta"])

    def test_02_aggregation_and_reversal_flag(self):
        data = client.get(URL).json()
        p = data["pairs"][0]
        # 同评委差值合计（有序对 a=B − b=A）= (8−9) + (8−9) = −2
        # （I3 A 缺项目，不计 0 分）。
        self.assertAlmostEqual(p["item_delta_sum"], -2.0)
        self.assertTrue(p["comparable"])
        self.assertEqual(p["comparable_item_count"], 2)
        self.assertEqual(p["incomparable_item_count"], 1)
        # 全量总分：B=(8+8+10)/3 + 8 + 20 ≈ 36.667 领先；同评委口径 A（有序对 b）
        # 领先 → 两口径方向相反，标记反向。
        self.assertAlmostEqual(p["full_total_a"], 36.6666666667, places=6)
        self.assertAlmostEqual(p["full_total_b"], 18.0)
        self.assertEqual(p["full_lead"], "a")
        self.assertEqual(p["lead"], "b")
        self.assertTrue(p["reversed"])
        self.assertEqual(data["summary"]["pairs_reversed"], 1)
        self.assertEqual(data["summary"]["incomparable_reasons"]["b_missing"], 1)

    def test_03_common_judge_detail_rows(self):
        p = client.get(URL).json()["pairs"][0]
        i1 = next(row for row in p["items"] if row["item"] == "I1")
        ja = next(j for j in i1["common_judges"] if j["judge"] == "JA")
        self.assertEqual(set(ja), {"judge", "score_a", "score_b"})
        # 明细给分顺序与有序对一致（a = B 得 8，b = A 得 9）。
        self.assertAlmostEqual(ja["score_a"], 8.0)
        self.assertAlmostEqual(ja["score_b"], 9.0)

    def test_04_readonly_does_not_mutate(self):
        before = client.get(f"/api/sessions/{SID}/analysis").json()
        for _ in range(2):
            client.get(URL)
        after = client.get(f"/api/sessions/{SID}/analysis").json()
        self.assertEqual(
            [(b["contestant"], b["eff_total"]) for b in before["contestants"]],
            [(b["contestant"], b["eff_total"]) for b in after["contestants"]],
        )
        self.assertEqual(after["counts"]["decisions"], before["counts"]["decisions"])

    def test_05_recomputed_after_decision_change(self):
        # 选一条「校准分 ≠ 原始分」的评分：B 由 JB 在 I1 打的 8（校准为 9）。
        # 恢复采用校准分会改变 I1 的 B 共同均分，同评委差值随之变化。
        analysis = client.get(f"/api/sessions/{SID}/analysis").json()
        target = next(
            s for b in analysis["contestants"] for ib in b["items"] for s in ib["scores"]
            if b["contestant"] == "B" and ib["item"] == "I1" and s["judge"] == "JB"
        )
        self.assertNotAlmostEqual(target["calibrated"], target["raw"])
        before = client.get(URL).json()["pairs"][0]
        client.patch(
            f"/api/sessions/{SID}/scores/{target['id']}/decision",
            json={"use_raw": False, "reason": ""},
        )
        after = client.get(URL).json()["pairs"][0]
        self.assertNotAlmostEqual(
            before["item_delta_sum"], after["item_delta_sum"]
        )
        # 还原：重新保留原分，值应回到原值。
        client.patch(
            f"/api/sessions/{SID}/scores/{target['id']}/decision",
            json={"use_raw": True, "reason": "同评委对照测试"},
        )
        restored = client.get(URL).json()["pairs"][0]
        self.assertAlmostEqual(
            restored["item_delta_sum"], before["item_delta_sum"]
        )

    def test_06_scope_change_reflected(self):
        self.assertEqual(client.get(URL).json()["scope"], "judge")
        client.post(f"/api/sessions/{SID}/scope", json={"scope": "judge_item"})
        self.assertEqual(client.get(URL).json()["scope"], "judge_item")
        client.post(f"/api/sessions/{SID}/scope", json={"scope": "judge"})

    def test_07_unknown_and_empty_session(self):
        # 会话不存在（从未上传）→ 404。
        r = client.get("/api/sessions/nope/same-judge-compare")
        self.assertEqual(r.status_code, 404)
        r2 = client.get("/api/sessions/empty-sj/same-judge-compare")
        self.assertEqual(r2.status_code, 404)


class TestSameJudgeNoCommonAPI(unittest.TestCase):
    def test_disjoint_judges_pair_is_incomparable(self):
        sid = "samejudge-disjoint"
        client.delete(f"/api/sessions/{sid}")
        body = (
            "选手,评委,项目,分数\n"
            "X,JA,I1,9\nX,JB,I2,9\n"
            "Y,JB,I1,9\nY,JA,I2,9\n"
        )
        r = client.post(
            f"/api/sessions/{sid}/upload?scope=judge",
            files={"file": ("d.csv", body.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = client.get(f"/api/sessions/{sid}/same-judge-compare").json()
        self.assertEqual(data["summary"]["pairs_total"], 1)
        p = data["pairs"][0]
        self.assertFalse(p["comparable"])
        self.assertEqual(p["status"], "incomparable")
        self.assertIsNone(p["item_delta_sum"])
        self.assertIsNone(p["lead"])
        self.assertFalse(p["reversed"])
        for row in p["items"]:
            self.assertEqual(row["status"], "no_common_judge")
        self.assertEqual(
            data["summary"]["incomparable_reasons"]["no_common_judge"], 2
        )
        client.delete(f"/api/sessions/{sid}")


if __name__ == "__main__":
    unittest.main()
