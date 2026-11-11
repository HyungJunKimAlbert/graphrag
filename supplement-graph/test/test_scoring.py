#!/usr/bin/env python3
"""3단계 점수제 확인. 그래프·LLM 없이 combine() 만 돌린다.

    python3 test/test_scoring.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from reco.pipeline import card, combine, top_n  # noqa: E402


def path(sid, mech="에너지 대사 저하", fn="피로·활력 개선", lifecycle=False):
    return {"symptom_id": sid, "symptom": sid, "mechanism": mech, "function": fn, "via": "function",
            "lifecycle": lifecycle, "confirmed": True, "sources": ["질병관리청"], "urls": []}


def sym(iid, *paths):
    return {"ingredient_id": iid, "name": iid, "kind": "Nutrient", "paths": list(paths),
            "n_symptoms": len({p["symptom_id"] for p in paths}), "n_lifecycle": 0, "n_confirmed": 1}


def intr(iid):
    return {"ingredient_id": iid, "name": iid, "kind": "Nutrient", "interests": [{"interest": "활력"}],
            "interest_order": (1, 1), "function_text": "x"}


def drug(iid):
    return {"ingredient_id": iid, "name": iid, "kind": "Nutrient", "function_text": "x",
            "depletions": [{"drug_id": "D", "drug": "약", "grade": "A", "effect": "결핍", "quote": "q", "source": "식약처"}]}


FAIL = 0


def check(name, cond, detail=""):
    global FAIL
    FAIL += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if not cond else ""))


def main():
    indirect = path("S2", "심리적 스트레스", "수면 건강")
    out = combine(
        {"SDI": sym("SDI", path("S1")), "SD": sym("SD", path("S1")), "SI": sym("SI", path("S1")),
         "S2direct": sym("S2direct", path("S1"), path("S2")), "S1direct": sym("S1direct", path("S1"), indirect),
         "S": sym("S", path("S1")), "Slife": sym("Slife", path("S3", lifecycle=True)),
         "SlifeInd": sym("SlifeInd", path("S3", "심리적 스트레스", "수면 건강", lifecycle=True))},
        {"SDI": intr("SDI"), "SI": intr("SI"), "DI": intr("DI"), "I": intr("I")},
        {"SDI": drug("SDI"), "SD": drug("SD"), "DI": drug("DI"), "D": drug("D")},
        {"S": [{"drug": "약", "grade": "B", "effect": "참고"}]})
    ids = [c["ingredient_id"] for c in out]
    by = {c["ingredient_id"]: c for c in out}
    check("세 갈래가 겹치면 3점으로 맨 위", ids[0] == "SDI" and by["SDI"]["score"] == 3, ids)
    check("2점 동점: 증상+복용약 > 증상+관심사 > 복용약+관심사", ids[1:4] == ["SD", "SI", "DI"], ids)
    check("1점 동점: 증상 > 복용약 > 관심사", ids.index("S") < ids.index("D") < ids.index("I"), ids)
    check("증상만 있는 후보끼리는 직접 경로 증상 수가 많은 쪽이 먼저",
          ids.index("S2direct") < ids.index("S1direct") and by["S1direct"]["n_direct"] == 1, ids)
    check("생애주기 우선 증상을 직접 덮는 후보가 증상을 더 많이 덮는 후보보다 먼저",
          ids.index("Slife") < ids.index("S2direct") and by["Slife"]["n_priority"] == 1, ids)
    check("간접 경로로만 닿은 생애주기 증상은 우선 증상으로 세지 않는다",
          by["SlifeInd"]["n_priority"] == 0 and ids.index("S2direct") < ids.index("SlifeInd"), ids)
    check("B 등급은 점수를 주지 않고 참고로만 붙는다", by["S"]["score"] == 1 and len(by["S"]["drug_notes"]) == 1)
    check("같은 원료는 한 번만 나온다", len(ids) == len(set(ids)) == 11, ids)
    # 주의가 걸린 원료: 순위가 밀리지 않고, 카드에 주의 문구가 그대로 실린다
    ranked = combine({"S2": sym("S2", path("a"), path("b")), "S1": sym("S1", path("a"))}, {})
    by_id = {c["ingredient_id"]: c for c in ranked}
    by_id["S2"]["cautions"], by_id["S1"]["cautions"] = ["항응고제 복용 시 전문가와 상담할 것"], []
    top = top_n(ranked)
    check("주의가 걸려도 우선순위가 높으면 그대로 맨 위", top[0]["ingredient_id"] == "S2", [c["ingredient_id"] for c in top])
    check("카드에 주의 문구가 원문 그대로 실린다", card(top[0])["cautions"] == ["항응고제 복용 시 전문가와 상담할 것"] and card(top[1])["cautions"] == [])
    print(f"\n{10 - FAIL}/10 통과")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
