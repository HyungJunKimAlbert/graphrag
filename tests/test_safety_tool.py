"""Task 6. 금기 거부권. rag DB와 기존 엔진의 80세 남성 픽스처를 쓴다. LLM, 임베딩, 검색기는 가짜다.

80세 남성 픽스처: 와파린·글리메피리드·메트포르민·로사르탄 복용, GFR 51.
이 환자에게 오메가-3와 마그네슘은 금기, 비타민 B12는 제한 없음, 정어리펩타이드는 주의(상담 필요)다.
"""
import copy
import json

import pytest

from wcrag import config, legacy
from wcrag.answer import guard, safety_tool
from wcrag.answer.ask import NO_PATIENT_NOTICE, ask

PATIENT = json.loads((config.LEGACY_GRAPHDB / "tests" / "fixtures" / "patient_80m.json").read_text(encoding="utf-8"))
OMEGA3, B12, SARDINE = "IND-2014-36", "NUT-45669", "IND-2004-1"
NAMES = {OMEGA3: "오메가-3 지방산 함유 유지", B12: "비타민 B12", SARDINE: "정어리펩타이드 SP100N"}
CONTEXT = {
    "seeds": [{"key": f"Ingredient:{OMEGA3}", "kind": "Ingredient", "name": NAMES[OMEGA3], "score": 0.05, "isolated": False}],
    "facts": [{"id": "F-omega00001", "text": f"원료 '{NAMES[OMEGA3]}'은(는) 기능성 분류 '혈중 중성지방'에 속한다.",
               "start": f"Ingredient:{OMEGA3}", "type": "HAS_FUNCTION", "end": "FunctionTag:TAG-tg", "sources": []},
              {"id": "F-b120000001", "text": f"약 '메트포르민'은(는) 원료 '{NAMES[B12]}'을(를) 고갈시킬 수 있다.",
               "start": "Drug:DRUG-metformin", "type": "DEPLETES", "end": f"Ingredient:{B12}", "sources": []},
              {"id": "F-sardine001", "text": f"원료 '{NAMES[SARDINE]}'은(는) 기능성 분류 '혈압'에 속한다.",
               "start": f"Ingredient:{SARDINE}", "type": "HAS_FUNCTION", "end": "FunctionTag:TAG-bp", "sources": []}],
    "docs": [], "ingredient_ids": [OMEGA3, B12, SARDINE], "ingredient_names": NAMES, "max_seed_score": 0.05,
}
ROUTE = {"red_flag": False, "red_flag_reason": "", "modes": ["local"], "patient": None, "mentions": []}
RECOMMEND_OMEGA3 = {"answerable": True,
                    "claims": [{"text": f"{NAMES[OMEGA3]}를 검토할 수 있습니다.", "evidence_ids": ["F-omega00001"]}],
                    "recommended_ingredient_ids": [OMEGA3], "cautioned_ingredient_ids": []}
CAUTION_OMEGA3 = {"answerable": True,
                  "claims": [{"text": f"{NAMES[OMEGA3]}는 와파린을 복용하는 이 환자에게는 피해야 합니다.",
                              "evidence_ids": ["F-omega00001"]}],
                  "recommended_ingredient_ids": [], "cautioned_ingredient_ids": [OMEGA3]}
RECOMMEND_B12 = {"answerable": True,
                 "claims": [{"text": f"메트포르민 복용으로 부족해질 수 있는 {NAMES[B12]}를 검토할 수 있습니다.",
                             "evidence_ids": ["F-b120000001"]}],
                 "recommended_ingredient_ids": [B12], "cautioned_ingredient_ids": []}


class EngineDb:
    """기존 엔진이 기대하는 db.run 모양. 테스트에서 엔진을 개발 DB에 직접 돌릴 때만 쓴다."""

    def __init__(self, graph):
        self.graph = graph

    def run(self, cypher, **params):
        return self.graph.read(cypher, **params)


class FakeLLM:
    def __init__(self, draft, route=None):
        self.draft, self.route, self.calls = draft, route or ROUTE, []

    def __call__(self, instructions, payload, schema, tag):
        self.calls.append({"tag": tag, "payload": payload})
        return schema.model_validate(self.route if tag == "analyze" else self.draft)

    def payload(self, tag):
        return next(c["payload"] for c in self.calls if c["tag"] == tag)


def run(rag, draft, patient=PATIENT, route=None):
    llm = FakeLLM(draft, route)
    retriever = lambda graph, question, embed, **kw: copy.deepcopy(CONTEXT)   # noqa: E731
    result = ask(rag, "질문", patient, "local", embed=lambda texts: [[0.0]] * len(texts), complete=llm,
                 retrievers={"local": retriever, "vector": retriever})
    return result, llm


def hint(**over):
    return {**ROUTE, "patient": {"age": None, "sex": None, "conditions": [], "drugs": [], "pregnant": None,
                                 "lactating": None, **over}}


# ---------- 기존 엔진과 같은 판정 ----------

def test_verdicts_equal_engine(rag, dev):
    from recommend import normalize, safety
    ids = [r[0] for r in rag.read("MATCH (i:Ingredient) RETURN i.ingredient_id ORDER BY i.ingredient_id")]
    engine = EngineDb(dev)
    expected = safety.check(engine, ids, normalize.build_context(engine, PATIENT))

    got = safety_tool.verdicts(rag, PATIENT, ids)

    assert got["by_ingredient"] == expected
    assert not expected[OMEGA3]["allowed"] and expected[B12]["allowed"]   # 빈 것끼리 비교한 게 아님


def test_avoid_ids_lists_every_forbidden_ingredient(rag):
    avoid = safety_tool.avoid_ids(rag, PATIENT)
    assert OMEGA3 in avoid and B12 not in avoid and SARDINE not in avoid
    assert avoid == sorted(avoid)


def test_engine_forbidden_expressions_are_reused():
    from recommend import llm as engine_llm
    assert legacy.FORBIDDEN is engine_llm.FORBIDDEN
    assert guard.FORBIDDEN is legacy.FORBIDDEN


def test_legacy_adapter_cannot_write(rag):
    with pytest.raises(Exception):
        legacy.ReadOnly(rag).run("CREATE (:_LegacyWriteProbe)")
    assert rag.read("MATCH (n:_LegacyWriteProbe) RETURN count(n)")[0][0] == 0


# ---------- 거부권 ----------

def test_warfarin_patient_omega3_recommendation_blocked(rag):
    result, _ = run(rag, RECOMMEND_OMEGA3)
    assert result["status"] == "blocked"
    assert result["claims"] == [] and result["recommended"] == []
    assert result["safety_checked"] is True
    assert NAMES[OMEGA3] in result["text"] and "와파린" in result["text"]
    assert "검토할 수 있습니다" not in result["text"]          # 막힌 답변 문장은 보이지 않는다


def test_mention_as_caution_is_allowed(rag):
    result, _ = run(rag, CAUTION_OMEGA3)
    assert result["status"] == "answered"
    assert result["cautioned"] == [OMEGA3] and result["safety_checked"] is True
    assert NO_PATIENT_NOTICE not in result["text"]


def test_allowed_ingredient_recommendation_passes(rag):
    result, _ = run(rag, RECOMMEND_B12)
    assert result["status"] == "answered" and result["recommended"] == [B12]


def test_forbidden_ingredient_is_marked_before_generation(rag):
    _, llm = run(rag, CAUTION_OMEGA3)
    lines = llm.payload("generate").splitlines()
    marked = [line for line in lines if "[이 환자에게 금기:" in line]
    assert any(line.startswith("F-omega00001") for line in marked)
    assert any(line.startswith(OMEGA3) for line in marked)
    assert not any(line.startswith(("F-b120000001", B12)) for line in marked)


def test_caution_finding_is_attached_to_recommendation(rag):
    draft = {"answerable": True,
             "claims": [{"text": f"{NAMES[SARDINE]}를 검토할 수 있습니다.", "evidence_ids": ["F-sardine001"]}],
             "recommended_ingredient_ids": [SARDINE], "cautioned_ingredient_ids": []}
    result, _ = run(rag, draft)
    assert result["status"] == "answered"
    assert any(NAMES[SARDINE] in w and "상담" in w for w in result["warnings"])


def test_guard_blocks_recommended_avoid_but_not_cautioned():
    verdicts = {"by_ingredient": {OMEGA3: {"allowed": False, "caution": [],
                                           "avoid": [{"level": "avoid", "by": "와파린", "text": "출혈 위험"}]}},
                "incomplete": []}
    ok, reasons = guard.check(RECOMMEND_OMEGA3, CONTEXT, verdicts)
    assert not ok and any(NAMES[OMEGA3] in r and "출혈 위험" in r for r in reasons)
    assert guard.blocked(RECOMMEND_OMEGA3, CONTEXT, verdicts)
    assert guard.check(CAUTION_OMEGA3, CONTEXT, verdicts) == (True, [])
    assert guard.blocked(CAUTION_OMEGA3, CONTEXT, verdicts) == []
    assert guard.check(RECOMMEND_OMEGA3, CONTEXT, None) == (True, [])   # 판정이 없으면 거부권도 없다


# ---------- 판정이 불완전하거나 못 하는 경우 ----------

def test_unknown_drug_sets_incomplete(rag):
    patient = {"age": 60, "sex": "M", "drugs": [{"name": "가상약제XYZ"}]}
    assert any("가상약제XYZ" in reason for reason in safety_tool.verdicts(rag, patient, [B12])["incomplete"])

    result, _ = run(rag, RECOMMEND_B12, patient=patient)
    assert result["status"] == "answered" and result["safety_checked"] is True
    assert any("가상약제XYZ" in reason for reason in result["safety_incomplete"])
    assert any("가상약제XYZ" in w for w in result["warnings"])
    assert "금기 판정이 불완전" in result["text"]


def test_complete_judgement_has_no_incomplete_notice(rag):
    patient = {"age": 60, "sex": "M", "drugs": [{"name": "메트포르민"}]}
    result, _ = run(rag, RECOMMEND_B12, patient=patient)
    assert result["safety_incomplete"] == [] and "금기 판정이 불완전" not in result["text"]


def test_no_age_or_sex_means_unchecked(rag):
    for kwargs in ({"patient": None, "route": hint(drugs=["와파린"])},        # 질문에서 뽑은 단서
                   {"patient": {"drugs": [{"name": "와파린"}]}},               # 넘겨받은 환자 정보
                   {"patient": None, "route": hint(age=70, drugs=["와파린"])}):  # 성별만 없음
        result, _ = run(rag, RECOMMEND_OMEGA3, **kwargs)
        assert result["status"] == "answered" and result["safety_checked"] is False
        assert any("나이" in w and "성별" in w for w in result["warnings"])
        assert "금기를 판정하지 못했습니다" in result["text"]


def test_hint_with_age_and_sex_is_judged(rag):
    result, _ = run(rag, RECOMMEND_OMEGA3, patient=None, route=hint(age=70, sex="M", drugs=["와파린"]))
    assert result["status"] == "blocked" and result["safety_checked"] is True


def test_given_patient_identity_wins_but_question_drugs_are_added(rag):
    """나이·성별·임신은 환자 파일이 맞고, 질문에만 적힌 약·질환은 판정에 더한다(빠지면 금기를 놓친다)."""
    result, _ = run(rag, RECOMMEND_OMEGA3, patient={"age": 40, "sex": "F", "pregnant": False},
                    route=hint(age=70, sex="M", drugs=["와파린"]))
    assert result["status"] == "blocked" and "와파린" in result["text"]


def test_merge_patient_keeps_identity_and_unions_drugs_and_conditions():
    merged = safety_tool.merge_patient(
        {"age": 40, "sex": "F", "pregnant": False, "drugs": [{"name": "메트포르민"}], "conditions": ["당뇨"]},
        {**hint(age=70, sex="M", drugs=["와파린", "메트포르민"], conditions=["고혈압", "당뇨"])["patient"]})
    assert merged["age"] == 40 and merged["sex"] == "F" and merged["pregnant"] is False
    assert [d["name"] for d in merged["drugs"]] == ["메트포르민", "와파린"]
    assert merged["conditions"] == ["당뇨", "고혈압"]
    assert safety_tool.merge_patient(None, None) is None
    assert safety_tool.merge_patient({}, hint(age=70, sex="M")["patient"])["age"] == 70   # 빈 파일은 단서를 쓴다


def test_patient_complaint_reaches_red_flag_analysis(rag):
    _, llm = run(rag, RECOMMEND_B12)
    assert PATIENT["complaint"] in llm.payload("analyze")
