"""대체 추천. 질문에 답할 근거가 없을 때 나이·성별 → 구분(Segment) → 관심사 → 성분으로 일반 추천을 붙인다.

LLM 없이 기존 엔진의 관심사 생성기와 금기 판정을 그대로 쓴다. rag DB와 기존 엔진의 80세 남성 픽스처를 쓴다.
80세 남성 픽스처: 와파린·글리메피리드·메트포르민·로사르탄 복용, GFR 51.
"""
import copy
import json

from wcrag import config, legacy
from wcrag.answer import fallback, safety_tool
from wcrag.answer.ask import NO_EVIDENCE_TEXT, ask

PATIENT = json.loads((config.LEGACY_GRAPHDB / "tests" / "fixtures" / "patient_80m.json").read_text(encoding="utf-8"))
ROUTE = {"red_flag": False, "red_flag_reason": "", "modes": ["local"], "patient": None, "mentions": []}
EMPTY = {"seeds": [], "facts": [], "docs": [], "ingredient_ids": [], "ingredient_names": {}, "max_seed_score": 0.0}
B12 = "NUT-45669"
WITH_FACT = {
    "seeds": [{"key": "Drug:DRUG-metformin", "kind": "Drug", "name": "메트포르민", "score": 0.05, "isolated": False}],
    "facts": [{"id": "F-b120000001", "text": "약 '메트포르민'은(는) 원료 '비타민 B12'을(를) 고갈시킬 수 있다.",
               "start": "Drug:DRUG-metformin", "type": "DEPLETES", "end": f"Ingredient:{B12}", "sources": []}],
    "docs": [], "ingredient_ids": [B12], "ingredient_names": {B12: "비타민 B12"}, "max_seed_score": 0.05,
}
UNANSWERABLE = {"answerable": False, "claims": [], "recommended_ingredient_ids": [], "cautioned_ingredient_ids": []}
NO_CITATION = {"answerable": True, "claims": [{"text": "비타민 B12를 검토할 수 있습니다.", "evidence_ids": []}],
               "recommended_ingredient_ids": [B12], "cautioned_ingredient_ids": []}


class FakeLLM:
    def __init__(self, draft=None, route=None):
        self.draft, self.route, self.calls = draft or UNANSWERABLE, route or ROUTE, []

    def __call__(self, instructions, payload, schema, tag):
        self.calls.append(tag)
        return schema.model_validate(self.route if tag == "analyze" else self.draft)


def run(graph, patient=PATIENT, context=EMPTY, draft=None, route=None):
    llm = FakeLLM(draft, route)
    retriever = lambda graph, question, embed, **kw: copy.deepcopy(context)   # noqa: E731
    result = ask(graph, "질문", patient, "local", embed=lambda texts: [[0.0]] * len(texts), complete=llm,
                 retrievers={"local": retriever, "vector": retriever})
    return result, llm


# ---------- recommend: 규칙 ----------

def test_needs_age_and_sex():
    assert fallback.recommend(None, None) is None
    assert fallback.recommend(None, {"sex": "M", "drugs": []}) is None      # 나이 없음
    assert fallback.recommend(None, {"age": 70, "drugs": []}) is None       # 성별 없음


def test_follows_engine_interest_order_and_drops_forbidden(rag):
    got = fallback.recommend(rag, PATIENT)
    ctx = legacy.build_context(rag, PATIENT)
    order = list(dict.fromkeys(c["ingredient_id"] for c in legacy.interest_candidates(rag, ctx)))
    avoid = set(safety_tool.avoid_ids(rag, PATIENT))
    picked = [p["ingredient_id"] for p in got["picked"]]
    assert got["segment"] == ctx["segment"] and got["segment"] is not None
    assert picked == [i for i in order if i not in avoid][:fallback.N]    # 엔진 순서대로, 금기를 건너뛴 상위 N개
    assert {b["ingredient_id"] for b in got["blocked"]} <= avoid
    assert all(b["reasons"] for b in got["blocked"])
    assert all(p["paths"] and p["name"] for p in got["picked"])


def test_equals_engine_on_dev(rag, dev):
    """rag 복제본에서 낸 결과가 개발 DB에서 엔진 함수를 직접 돌린 결과와 같다."""
    from recommend import candidates, normalize, safety

    class EngineDb:
        def run(self, cypher, **params):
            return dev.read(cypher, **params)

    ctx = normalize.build_context(EngineDb(), PATIENT)
    ids = list(dict.fromkeys(c["ingredient_id"] for c in candidates.interest(EngineDb(), ctx)))   # 첫 등장 순서
    verdicts = safety.check(EngineDb(), ids, ctx)
    expected = [i for i in ids if verdicts[i]["allowed"]][:fallback.N]
    assert [p["ingredient_id"] for p in fallback.recommend(rag, PATIENT)["picked"]] == expected


def test_same_ingredient_in_two_interests_appears_once_with_both_paths(rag):
    """관심사 둘에 같은 성분이 있으면 한 번만 내고 근거를 합친다(엔진 select와 같은 처리)."""
    for segment in [r[0] for r in rag.read("MATCH (s:Segment) RETURN s.code")]:
        rows = legacy.interest_candidates(rag, {"segment": {"code": segment, "name": ""}})
        ids = [c["ingredient_id"] for c in rows]
        dup = next((i for i in ids if ids.count(i) > 1), None)
        if dup:
            sex, age = rag.read("MATCH (s:Segment {code: $c}) RETURN s.sex, s.minAge", c=segment)[0]
            got = fallback.recommend(rag, {"age": max(age, 20), "sex": sex, "drugs": [], "conditions": []}, n=50)
            hits = [p for p in got["picked"] + got["blocked"] if p["ingredient_id"] == dup]
            assert len(hits) == 1
            assert len(hits[0]["paths"]) == len({p for c in rows if c["ingredient_id"] == dup for p in c["paths"]})
            return
    # 그래프에 겹치는 성분이 없으면 확인할 것이 없다


# ---------- ask: 발동 지점 ----------

def test_empty_context_with_patient_falls_back_without_generating(rag):
    result, llm = run(rag)
    expected = fallback.recommend(rag, PATIENT)
    assert result["status"] == "fallback"
    assert result["recommended"] == [p["ingredient_id"] for p in expected["picked"]] and result["recommended"]
    assert result["text"].startswith(NO_EVIDENCE_TEXT)                    # 근거 없음을 먼저 밝힌다
    assert "개인 맞춤" in result["text"] and expected["segment"]["name"] in result["text"]
    assert all(result["ingredient_names"][i] in result["text"] for i in result["recommended"])
    assert result["safety_checked"] is True
    assert llm.calls == ["analyze"]                                       # 대체 추천은 LLM을 부르지 않는다
    assert {e["id"] for e in result["evidence"]} == {f"Ingredient:{i}" for i in result["recommended"]}


def test_blocked_candidates_are_listed_in_warnings(rag):
    expected = fallback.recommend(rag, PATIENT)
    result, _ = run(rag)
    for b in expected["blocked"]:
        assert any(b["name"] in w and "금기" in w for w in result["warnings"])


def test_unanswerable_with_patient_falls_back(rag):
    result, llm = run(rag, context=WITH_FACT, draft=UNANSWERABLE)
    assert result["status"] == "fallback" and llm.calls == ["analyze", "generate"]


def test_question_hint_with_age_and_sex_is_enough(rag):
    route = {**ROUTE, "patient": {"age": 72, "sex": "M", "conditions": [], "drugs": ["와파린"], "pregnant": None,
                                  "lactating": None}}
    result, _ = run(rag, patient=None, route=route)
    assert result["status"] == "fallback"


def test_without_age_or_sex_stays_no_evidence(rag):
    for patient in (None, {"sex": "M", "drugs": []}, {"age": 70, "drugs": []}):
        result, _ = run(rag, patient=patient)
        assert result["status"] == "no_evidence" and result["recommended"] == []


def test_rejected_answer_does_not_fall_back(rag):
    result, _ = run(rag, context=WITH_FACT, draft=NO_CITATION)
    assert result["status"] == "rejected" and result["recommended"] == []
