import re

import pytest

from rxrag import candidates, recommend as rec
from rxrag.llm import FakeLLM, MissingResponse, SessionLLM
from rxrag.recommend import recommend

FACT = re.compile(r"\b(?:" + "|".join(candidates.KINDS) + r"):[^\s:|/]+")
NO_FLAG = {"matches": [], "unmatched": [], "red_flag": False, "red_flag_reason": ""}


def table_rows(user):
    """select 프롬프트의 후보 표 → [(iid, name, fact_ids)]."""
    rows = []
    for line in user.split("[후보 목록]\n", 1)[1].split("\n\n", 1)[0].splitlines()[1:]:
        iid, name, facts, _ = [x.strip() for x in line.split(" | ", 3)]
        rows.append((iid, name.split(" (묶음:")[0], FACT.findall(facts), name))
    return rows


def good_picks(user, skip=()):
    picks, names, ids = [], set(), set()
    for iid, name, facts, label in table_rows(user):
        compact = name.replace(" ", "")
        if iid in skip or compact in names or "묶음:" in label or any(i in label for i in ids):
            continue
        picks.append({"ingredient_id": iid, "rank": len(picks) + 1, "fact_ids": facts[:1],
                      "reason": "근거 경로상 도움을 줄 수 있습니다."})
        names.add(compact)
        ids.add(iid)
        if len(picks) == 3:
            break
    return {"picks": picks}


def smart(normalize=NO_FLAG, bad_first=0):
    state = {"select": 0}

    def answer(task, system, user, schema):
        if task == "normalize":
            return normalize
        state["select"] += 1
        if state["select"] <= bad_first:
            return {"picks": [{"ingredient_id": "NOT-IN-POOL", "rank": 1, "fact_ids": ["x"], "reason": "r"}]}
        return good_picks(user)
    return FakeLLM(answer)


def select_calls(f):
    return [c for c in f.calls if c["task"] == "select"]


def test_invalid_input():
    r = recommend({"sex": "M"}, FakeLLM({}))
    assert r["status"] == "invalid_input" and r["recommendations"] == [] and r["message"]


@pytest.mark.graph
def test_red_flag_stops(patient_80m):
    f = smart(normalize={**NO_FLAG, "red_flag": True, "red_flag_reason": "흉통"})
    r = recommend(patient_80m, f)
    assert r["status"] == "stopped" and r["message"] == "추천을 멈추고 진료를 권유합니다."
    assert r["trace"]["red_flag_reason"] == "흉통" and select_calls(f) == []


@pytest.mark.graph
def test_happy_path_80m(patient_80m):
    f = smart(normalize={**NO_FLAG, "matches": [{"category": "스트레스/수면", "symptoms": ["새벽 각성"]}]})
    r = recommend(patient_80m, f)
    assert r["status"] in ("ok", "needs_review") and r["selector"] == "llm"
    assert len(r["recommendations"]) == 3 and all(x["evidence"] for x in r["recommendations"])
    excluded = {e["ingredient_id"] for e in r["excluded"]}
    assert excluded and not excluded & {x["ingredient_id"] for x in r["recommendations"]}
    assert r["trace"]["candidate_count"] >= r["trace"]["kept_count"] > 0 and r["trace"]["rule_version"]


@pytest.mark.graph
def test_select_prompt_has_no_excluded(patient_80m):
    f = smart()
    r = recommend(patient_80m, f)
    user = select_calls(f)[0]["user"]
    assert all(f"\n{e['ingredient_id']} | " not in user for e in r["excluded"])


@pytest.mark.graph
def test_bad_then_good_retries_once(patient_80m):
    f = smart(bad_first=1)
    r = recommend(patient_80m, f)
    calls = select_calls(f)
    assert r["selector"] == "llm" and len(calls) == 2 and "이전 응답의 문제" in calls[1]["user"]


@pytest.mark.graph
def test_bad_twice_falls_back(patient_80m):
    f = smart(bad_first=2)
    r = recommend(patient_80m, f)
    excluded = {e["ingredient_id"] for e in r["excluded"]}
    assert r["selector"] == "fallback_rules" and len(select_calls(f)) == 2
    assert len(r["recommendations"]) == 3 and not excluded & {x["ingredient_id"] for x in r["recommendations"]}


@pytest.mark.graph
def test_partial_skips_llm(patient_80m, monkeypatch):
    real_pool = candidates.pool
    monkeypatch.setattr(rec.candidates, "pool", lambda db, ctx: [c for c in real_pool(db, ctx)
                                                               if c["name"] in ("테아닌", "비타민 B12")])
    f = smart()
    r = recommend(patient_80m, f)
    assert r["status"] == "partial" and select_calls(f) == [] and len(r["recommendations"]) == 2
    assert r["message"] == "추천 가능한 성분이 2개뿐입니다. 억지로 채우지 않았습니다."


@pytest.mark.graph
def test_pregnancy_unknown_warning():
    r = recommend({"age": 30, "sex": "F", "symptoms": ["불면"]}, smart())
    assert r["status"] == "needs_review"
    assert "임신·수유 여부가 입력되지 않았습니다. 해당하면 차단 규칙이 달라집니다." in r["warnings"]


@pytest.mark.graph
def test_missing_session_response_raises(tmp_path, patient_80m):
    with pytest.raises(MissingResponse):
        recommend(patient_80m, SessionLLM(tmp_path, mode="run"))


@pytest.mark.graph
def test_partial_when_candidates_clash(patient_80m, monkeypatch):
    """후보가 3개 이상이어도 묶음·구성 성분처럼 서로 겹치면 3개를 못 채운다 → LLM을 부르지 않고 partial."""
    real_pool = candidates.pool
    names = ("비타민 B군", "비타민 B12", "엽산")
    monkeypatch.setattr(rec.candidates, "pool", lambda db, ctx: [c for c in real_pool(db, ctx) if c["name"] in names])
    f = smart()
    r = recommend(patient_80m, f)
    assert r["status"] == "partial" and select_calls(f) == []
    assert r["message"] == f"추천 가능한 성분이 {len(r['recommendations'])}개뿐입니다. 억지로 채우지 않았습니다."
    assert len(r["recommendations"]) < 3


@pytest.mark.graph
def test_trace_records_each_selection_attempt(patient_80m):
    """LLM이 시도마다 무엇을 골랐고 왜 거부·통과됐는지 trace에 남긴다."""
    r = recommend(patient_80m, smart(bad_first=2))
    attempts = r["trace"]["selection"]
    assert [a["attempt"] for a in attempts] == [1, 2] and all(a["errors"] for a in attempts)
    assert attempts[0]["picks"]["picks"][0]["ingredient_id"] == "NOT-IN-POOL"
    assert any("후보 목록에 없는" in e for e in attempts[0]["errors"])


@pytest.mark.graph
def test_trace_selection_on_success(patient_80m):
    r = recommend(patient_80m, smart())
    (only,) = r["trace"]["selection"]
    assert only["attempt"] == 1 and only["errors"] == [] and len(only["picks"]["picks"]) == 3
