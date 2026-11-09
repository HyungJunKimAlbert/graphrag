import json

import pytest

from eval import harness
from tests.test_recommend import good_picks

MINI = [
    {"id": "M-1", "group": "t", "note": "", "expect_status": None, "must_not": ["NTC-2-9"],
     "patient": {"age": 80, "sex": "M", "drugs": [{"name": "와파린"}, {"name": "메트포르민"}],
                 "complaint": "다리에 쥐가 나요"}},
    {"id": "M-2", "group": "t", "note": "", "expect_status": "stopped", "must_not": [],
     "patient": {"age": 58, "sex": "M", "complaint": "가슴이 쥐어짜듯 아파요"}},
]


def answer_all(root):
    """세션이 하듯 남은 요청 파일마다 응답 파일을 쓴다."""
    for req in sorted((root / "requests").glob("*/*.json")):
        resp = root / "responses" / req.parent.name / req.name
        if resp.exists():
            continue
        body = json.loads(req.read_text(encoding="utf-8"))
        if body["task"] == "normalize":
            flag = "가슴" in body["user"]
            out = {"matches": [] if flag else [{"category": "신장/전해질", "symptoms": ["근육경련(쥐남)"]}],
                   "unmatched": [], "red_flag": flag, "red_flag_reason": "흉통" if flag else ""}
        else:
            out = good_picks(body["user"])
        resp.parent.mkdir(parents=True, exist_ok=True)
        resp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


def with_real_must_not():
    """정답은 손으로 적지 않고 eval/build_must_not의 계산기로 만든다(패치 전 손 정답은 마그네슘을 잘못 넣었다)."""
    from eval import build_must_not
    from rxrag import graph
    db = graph.connect()
    all_ids = [r[0] for r in db.run(build_must_not.Q_ALL)]
    return [{**p, "must_not": build_must_not.must_not(db, p["patient"], all_ids)} for p in MINI]


@pytest.mark.graph
def test_mini_must_not_has_warfarin_coq10():
    assert "NTC-2-9" in with_real_must_not()[0]["must_not"]


@pytest.mark.graph
def test_export_run_g1_end_to_end(tmp_path):
    MINI = with_real_must_not()
    root, out = tmp_path / "session", tmp_path / "eval"
    assert harness.export(MINI, root) == 2          # 1라운드: 정규화 요청 2개
    answer_all(root)
    assert harness.export(MINI, root) == 1          # 2라운드: 위험 신호 환자를 뺀 1명의 select 요청
    answer_all(root)
    assert harness.export(MINI, root) == 0
    results = harness.run(MINI, root, out / "t.jsonl")
    assert results[0]["result"]["status"] in ("ok", "needs_review") and results[1]["result"]["status"] == "stopped"
    assert results[0]["result"]["selector"] == "llm"
    assert harness.g1(MINI, results) == []
    assert len((out / "t.jsonl").read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.graph
def test_g1_catches_forbidden_and_foreign_fact(tmp_path):
    MINI = with_real_must_not()
    root = tmp_path / "session"
    harness.export(MINI, root); answer_all(root); harness.export(MINI, root); answer_all(root)
    results = harness.run(MINI, root, tmp_path / "t.jsonl")
    bad = json.loads(json.dumps(results))
    rec = bad[0]["result"]["recommendations"][0]
    rec["ingredient_id"] = "NTC-2-9"
    rec["evidence"][0]["fact_id"] = "SYM_DIRECT:FAKE>NTC-2-9"
    problems = harness.g1(MINI, bad)
    assert any("금지" in p for p in problems) and any("fact_id" in p for p in problems)


def test_g1_expect_status_mismatch():
    results = [{"id": "M-2", "result": {"status": "ok", "message": None, "recommendations": [], "excluded": [],
                                        "warnings": [], "selector": None, "trace": {}}}]
    assert any("status" in p for p in harness.g1([MINI[1]], results, check_facts=False))


def test_g2_aggregate_boundary():
    def v(w, worse=None):
        return {"new_side_result": w, "new_clearly_worse": worse}
    eight = [v("win")] * 6 + [v("tie")] * 2 + [v("loss")] * 2
    agg = harness.g2_aggregate(eight)
    assert agg["win"] == 6 and agg["tie"] == 2 and agg["loss"] == 2 and agg["ratio"] == 0.8 and agg["pass"]
    assert not harness.g2_aggregate([v("win")] * 7 + [v("loss")] * 3)["pass"]
    assert not harness.g2_aggregate([v("win")] * 9 + [v("loss", worse=True)])["pass"]


def test_judge_sides_deterministic_and_mapped():
    assert harness.new_side("V-01") == harness.new_side("V-01") and harness.new_side("V-01") in ("A", "B")
    assert harness.unblind({"winner": "A", "clearly_worse": "B"}, new="A") == {"new_side_result": "win",
                                                                             "new_clearly_worse": False}
    assert harness.unblind({"winner": "A", "clearly_worse": "B"}, new="B") == {"new_side_result": "loss",
                                                                             "new_clearly_worse": True}
    assert harness.unblind({"winner": "tie", "clearly_worse": None}, new="B")["new_side_result"] == "tie"


def test_judge_text_hides_system_tells():
    """새 시스템의 이유 문장과 엔진의 단계명은 어느 쪽인지 드러내므로 채점 문구에 넣지 않는다."""
    new = {"status": "ok", "recommendations": [{"ingredient_id": "A", "name": "테아닌", "reason": "LLM이 쓴 이유 문장",
           "evidence": [{"text": "새벽 각성 → 근거"}], "cautions": []}]}
    old = {"recommendations": [{"ingredient_id": "B", "name": "마그네슘", "tier": "1-1 증상 직접 근거",
           "paths": ["근육통 → 근거"], "cautions": []}]}
    patients = [{"id": "X", "patient": {"age": 50, "sex": "M"}}]
    (_, user), = harness.judge_requests(patients, [{"id": "X", "result": new}], [{"id": "X", "engine": old}])
    assert "LLM이 쓴 이유 문장" not in user and "1-1 증상 직접 근거" not in user
    assert "테아닌" in user and "마그네슘" in user and "새벽 각성 → 근거" in user and "근육통 → 근거" in user
