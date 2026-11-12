#!/usr/bin/env python3
"""3단계 점수제 확인. 그래프·LLM 없이 combine() 만 돌린다.

    python3 test/test_scoring.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from reco.pipeline import card, combine, conflict, split_fixed, symptom_slots, top_n  # noqa: E402


def path(sid, mech=None, fn="피로·활력 개선", lifecycle=False, confirmed=True):
    """confirmed=False 는 원본 엑셀에서 한 구간이라도 '부분' 판정인 경로(간접 경로)다."""
    return {"symptom_id": sid, "symptom": sid, "mechanism": mech or f"기전-{sid}", "function": fn, "via": "function",
            "lifecycle": lifecycle, "confirmed": confirmed, "sources": ["질병관리청"], "urls": []}


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
    indirect = path("S2", "심리적 스트레스", "수면 건강", confirmed=False)
    out = combine(
        {"SDI": sym("SDI", path("S1")), "SD": sym("SD", path("S1")), "SI": sym("SI", path("S1")),
         "S2direct": sym("S2direct", path("S1"), path("S2")), "S1direct": sym("S1direct", path("S1"), indirect),
         "S": sym("S", path("S1")), "Slife": sym("Slife", path("S3", lifecycle=True)),
         "SlifeInd": sym("SlifeInd", path("S3", "심리적 스트레스", "수면 건강", lifecycle=True, confirmed=False))},
        {"SDI": intr("SDI"), "SI": intr("SI"), "DI": intr("DI"), "I": intr("I")},
        {"SDI": drug("SDI"), "SD": drug("SD"), "DI": drug("DI"), "D": drug("D")},
        {"S": [{"drug": "약", "grade": "B", "effect": "참고"}]})
    ids = [c["ingredient_id"] for c in out]
    by = {c["ingredient_id"]: c for c in out}
    check("증상을 더 많이 덮는 원료가 점수와 상관없이 맨 위, 관심사도 1점",
          ids[0] == "S2direct" and by["SDI"]["score"] == 3 and by["I"]["score"] == 1, ids)
    check("덮는 증상 수가 같으면 점수 순, 2점 동점은 증상+복용약 > 증상+관심사 > 우선 증상 가점",
          ids[1:5] == ["SDI", "SD", "SI", "Slife"], ids)
    check("증상을 직접 덮지 않는 원료는 점수가 높아도 증상 1개 원료보다 뒤", ids.index("DI") > ids.index("S"), ids)
    check("생애주기 우선 증상을 직접 덮으면 1점을 더 받는다", by["Slife"]["score"] == 2 and by["Slife"]["priority_bonus"] == 1)
    check("간접 경로뿐이면 증상 점수를 받지 못한다", by["SlifeInd"]["score"] == 0 and by["SlifeInd"]["routes"] == [], by["SlifeInd"]["score"])
    ind = combine({"X": sym("X", path("S1", "심리적 스트레스", "수면 건강", confirmed=False))}, {"X": intr("X")})[0]
    check("간접 경로 + 관심사는 관심사 1점만 남고 카드도 관심사 근거로 나온다",
          ind["score"] == 1 and ind["routes"] == ["관심사"] and card(ind)["tag"] == "관심사", (ind["score"], ind["routes"]))
    check("1점 동점: 증상 > 복용약 > 관심사", ids.index("S") < ids.index("D") < ids.index("I"), ids)
    check("간접 경로는 덮는 증상 수에 세지 않는다",
          ids.index("S2direct") < ids.index("S1direct") and by["S1direct"]["n_direct"] == 1, ids)
    check("증상 2개를 덮는 원료가 생애주기 우선 증상 1개를 덮는 원료보다 먼저",
          ids.index("S2direct") < ids.index("Slife") and by["Slife"]["n_priority"] == 1, ids)
    check("간접 경로로만 닿은 생애주기 증상은 우선 증상으로 세지 않는다",
          by["SlifeInd"]["n_priority"] == 0 and ids[-1] == "SlifeInd", ids)
    check("전체 순서", ids == ["S2direct", "SDI", "SD", "SI", "Slife", "S1direct", "S", "DI", "D", "I", "SlifeInd"], ids)
    check("B 등급은 점수를 주지 않고 참고로만 붙는다", by["S"]["score"] == 1 and len(by["S"]["drug_notes"]) == 1)
    check("같은 원료는 한 번만 나온다", len(ids) == len(set(ids)) == 11, ids)
    # 주의가 걸린 원료: 순위가 밀리지 않고, 카드에 주의 문구가 그대로 실린다
    ranked = combine({"S2": sym("S2", path("a"), path("b")), "S1": sym("S1", path("a"))}, {})
    by_id = {c["ingredient_id"]: c for c in ranked}
    by_id["S2"]["cautions"], by_id["S1"]["cautions"] = ["항응고제 복용 시 전문가와 상담할 것"], []
    top = top_n(ranked)
    check("주의가 걸려도 우선순위가 높으면 그대로 맨 위", top[0]["ingredient_id"] == "S2", [c["ingredient_id"] for c in top])
    check("카드에 주의 문구가 원문 그대로 실린다", card(top[0])["cautions"] == ["항응고제 복용 시 전문가와 상담할 것"] and card(top[1])["cautions"] == [])
    # 근거 묶음: 근거가 같은 원료는 대표 하나만 자리를 갖고 나머지는 alternatives 로 붙는다
    g = combine({k: sym(k, path("S1")) for k in ("A1", "A2", "A3")} | {"B": sym("B", path("S2")), "C": sym("C", path("S3"))}, {})
    t = top_n(g, basics={"A2": {"intake_raw": "1 g", "precaution_text": "x"}})
    ids = [c["ingredient_id"] for c in t]
    check("근거가 같은 원료는 한 자리, 나머지는 같은 근거 원료로 남는다",
          ids == ["A2", "B", "C"] and sorted(a["ingredient_id"] for a in t[0]["alternatives"]) == ["A1", "A3"], ids)
    t = top_n(g, synergy={"A2": {"A3"}}, basics={"A2": {"intake_raw": "1 g", "precaution_text": "x"}})
    check("같은 근거라도 대표와 시너지 짝이면 따로 한 자리", [c["ingredient_id"] for c in t][:3] == ["A2", "A3", "B"],
          [c["ingredient_id"] for c in t])
    t = top_n(combine({k: sym(k, path("S1")) for k in ("A1", "A2", "A3")}, {}))
    check("묶음이 3개보다 적으면 같은 묶음의 다음 원료로 3개까지 채운다", len(t) == 3, [c["ingredient_id"] for c in t])
    # 선정: 3위보다 순서가 앞서는 원료는 확정, 3위와 순서가 같은 원료만 LLM 동점 후보
    sel = top_n(combine({"T3": sym("T3", path("a"), path("b"), path("c")), "T2": sym("T2", path("a"), path("d")),
                         "U1": sym("U1", path("e")), "U2": sym("U2", path("f")), "U3": sym("U3", path("g"))}, {}))
    fixed, pool, _ = split_fixed(sel, 3)
    check("증상 3개·2개 덮는 원료는 확정, 1개 덮는 원료끼리만 동점 후보",
          [c["ingredient_id"] for c in fixed] == ["T3", "T2"] and sorted(c["ingredient_id"] for c in pool) == ["U1", "U2", "U3"],
          ([c["ingredient_id"] for c in fixed], [c["ingredient_id"] for c in pool]))
    sel = top_n(combine({"T3": sym("T3", path("a"), path("b"), path("c")), "U1": sym("U1", path("e"))},
                        {"U1": intr("U1"), "X": intr("X"), "Y": intr("Y")}))
    fixed, pool, _ = split_fixed(sel, 3)
    check("증상 3개 원료가 증상 1개+관심사 원료보다 앞에서 확정",
          [c["ingredient_id"] for c in fixed] == ["T3", "U1"], [c["ingredient_id"] for c in sel])
    # 허브 기전 할인: 기전 하나로 설명되는 증상은 하나로 센다
    hub = combine({"H": sym("H", *(path(x, "여성호르몬 변화") for x in "abc")),
                   "K": sym("K", path("a", "m1"), path("b", "m2"))}, {})
    hb = {c["ingredient_id"]: c for c in hub}
    check("기전 하나로만 이어진 증상 3개는 1개로 센다", hb["H"]["n_direct"] == 1 and hb["K"]["n_direct"] == 2,
          (hb["H"]["n_direct"], hb["K"]["n_direct"]))
    check("서로 다른 기전 2개로 이어진 원료가 허브 기전 원료보다 앞", [c["ingredient_id"] for c in hub][0] == "K")
    # 증상 자리: 생애주기 가점으로 한 증상 원료가 몰려도 다른 증상 자리를 먼저 채운다
    ranked = top_n(combine({"A1": sym("A1", path("tired", lifecycle=True)), "A2": sym("A2", path("tired", "m2", lifecycle=True)),
                            "A3": sym("A3", path("tired", "m3", lifecycle=True)), "Z": sym("Z", path("wake"))}, {}), n=99)
    slots, missing = symptom_slots(ranked, ["wake", "tired"])
    check("증상마다 한 자리: 순위가 낮아도 덮이지 않은 증상(wake)의 원료가 들어간다",
          [c["ingredient_id"] for c in slots] == ["Z", "A1"] and missing == [], [c["ingredient_id"] for c in slots])
    slots, missing = symptom_slots(ranked, ["wake", "tired", "none"])
    check("덮을 원료가 없는 증상은 비워 두고 알린다", missing == ["none"], missing)
    # 같은 원료군
    g1 = {"ingredient_id": "a", "name": "인삼", "antagonists": []}
    g2 = {"ingredient_id": "b", "name": "홍삼", "antagonists": []}
    g3 = {"ingredient_id": "c", "name": "비타민 C", "antagonists": []}
    check("인삼과 홍삼은 같은 원료군이라 함께 고르지 않는다", conflict(g1, g2) and not conflict(g1, g3))
    total = 25
    print(f"\n{total - FAIL}/{total} 통과")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
