"""Task 5. 질문 분석 → 검색 → 답변 생성 → 검증. LLM, 임베딩, 검색기는 모두 가짜다."""
import copy

import pytest

from wcrag.answer import analyze as analyze_mod
from wcrag.answer import generate as generate_mod
from wcrag.answer import guard
from wcrag.answer.ask import ask

CONTEXT = {
    "seeds": [{"key": "Drug:DRUG-metformin", "kind": "Drug", "name": "메트포르민", "score": 0.05, "isolated": False},
              {"key": "Symptom:SYM-cramp", "kind": "Symptom", "name": "근육경련", "score": 0.04, "isolated": False}],
    "facts": [{"id": "F-aaaaaaaaaa", "text": "약 '메트포르민'은(는) 원료 '비타민 B12'을(를) 고갈시킬 수 있다.",
               "start": "Drug:DRUG-metformin", "type": "DEPLETES", "end": "Ingredient:NUT-45669", "sources": []},
              {"id": "F-bbbbbbbbbb", "text": "증상 '근육경련'에는 원료 '마그네슘'이(가) 연결돼 있다.",
               "start": "Symptom:SYM-cramp", "type": "RELIEVED_BY", "end": "Ingredient:NUT-45673", "sources": []}],
    "docs": [{"id": "DOC-intake-1", "kind": "intake", "text": "94.5~250 mg", "ingredient_id": "NUT-45673"},
             {"id": "DOC-function-1", "kind": "function", "text": "신경과 근육 기능 유지에 필요", "ingredient_id": "NUT-45673"}],
    "ingredient_ids": ["NUT-45663", "NUT-45669", "NUT-45673"],
    "ingredient_names": {"NUT-45663": "비타민 B1", "NUT-45669": "비타민 B12", "NUT-45673": "마그네슘"},
    "max_seed_score": 0.05,
}
ROUTE = {"red_flag": False, "red_flag_reason": "", "modes": ["local"], "patient": None, "mentions": []}
GOOD = {"answerable": True,
        "claims": [{"text": "근육경련에는 마그네슘이 연결돼 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
        "recommended_ingredient_ids": ["NUT-45673"], "cautioned_ingredient_ids": []}


class FakeLLM:
    """tag로 어느 단계의 호출인지 구분해 준비된 답을 돌려준다."""

    def __init__(self, route=None, draft=None):
        self.route, self.draft, self.calls = route or ROUTE, draft or GOOD, []

    def __call__(self, instructions, payload, schema, tag):
        self.calls.append({"tag": tag, "instructions": instructions, "payload": payload, "schema": schema})
        return schema.model_validate(self.route if tag == "analyze" else self.draft)


class Counter:
    def __init__(self, value=None):
        self.n, self.value = 0, value

    def __call__(self, *args, **kwargs):
        self.n += 1
        return self.value


def run(draft=None, route=None, context=CONTEXT, patient=None, mode="local"):
    llm = FakeLLM(route, draft)
    embed, retriever = Counter([[0.0]]), Counter(copy.deepcopy(context))
    result = ask(None, "질문", patient, mode, embed=embed, complete=llm, retrievers={"local": retriever, "vector": retriever})
    return result, llm, embed, retriever


def draft(**over):
    return {**copy.deepcopy(GOOD), **over}


# ── 질문 분석 ────────────────────────────────────────────────────────────

def test_analyze_keeps_only_enabled_modes():
    llm = FakeLLM(route={**ROUTE, "modes": ["cypher", "global"]})
    route = analyze_mod.analyze("수면 건강 원료는 몇 개?", llm)
    assert route["modes"] == ["local"]                    # 켜지 않은 방식만 고르면 기본 방식으로 돌아간다
    assert llm.calls[0]["tag"] == "analyze"


def test_analyze_returns_patient_hint():
    hint = {"age": 72, "sex": "M", "conditions": [], "drugs": ["와파린"], "pregnant": None, "lactating": None}
    route = analyze_mod.analyze("와파린 먹는 72세 남성", FakeLLM(route={**ROUTE, "patient": hint}))
    assert route["patient"] == hint


def test_analyze_returns_mentions():
    route = analyze_mod.analyze("다리에 쥐가 나는 환자", FakeLLM(route={**ROUTE, "mentions": ["근육경련"]}))
    assert route["mentions"] == ["근육경련"]


def test_ask_passes_mentions_to_retriever():
    seen = {}

    def retriever(graph, question, embed, mentions=None):
        seen["mentions"] = mentions
        return copy.deepcopy(CONTEXT)

    ask(None, "질문", None, "local", embed=Counter([[0.0]]), complete=FakeLLM(route={**ROUTE, "mentions": ["근육경련"]}),
        retrievers={"local": retriever, "vector": retriever})
    assert seen["mentions"] == ["근육경련"]


def test_red_flag_stops_before_retrieval():
    result, llm, embed, retriever = run(route={**ROUTE, "red_flag": True, "red_flag_reason": "흉통과 식은땀"})
    assert result["status"] == "stopped" and "흉통과 식은땀" in result["text"]
    assert embed.n == 0 and retriever.n == 0
    assert [c["tag"] for c in llm.calls] == ["analyze"]   # 답변 생성도 부르지 않는다


# ── 답변 생성 ────────────────────────────────────────────────────────────

def test_generate_restricts_ingredient_ids_to_context():
    llm = FakeLLM(draft=draft(recommended_ingredient_ids=["NOT-IN-CONTEXT"]))
    with pytest.raises(Exception):                         # 문맥에 없는 성분 ID는 출력 형식에서 막힌다
        generate_mod.generate("질문", CONTEXT, llm)


def test_generate_payload_lists_evidence_ids_and_isolated_seeds():
    context = copy.deepcopy(CONTEXT)
    context["seeds"].append({"key": "Symptom:SYM-x", "kind": "Symptom", "name": "혓바늘", "score": 0.03, "isolated": True})
    llm = FakeLLM()
    generate_mod.generate("질문", context, llm)
    payload = llm.calls[0]["payload"]
    assert "F-aaaaaaaaaa" in payload and "DOC-intake-1" in payload and "NUT-45673" in payload
    assert "혓바늘" in payload and "연결된 관계가 없" in payload


def test_generate_without_ingredients_returns_empty_lists():
    context = {**copy.deepcopy(CONTEXT), "ingredient_ids": [], "ingredient_names": {}}
    out = generate_mod.generate("질문", context, FakeLLM(draft={"answerable": True, "claims": GOOD["claims"]}))
    assert out["recommended_ingredient_ids"] == [] and out["cautioned_ingredient_ids"] == []


# ── 검증 ─────────────────────────────────────────────────────────────────

def test_valid_draft_is_answered_with_cited_evidence():
    result, *_ = run()
    assert result["status"] == "answered"
    assert "마그네슘" in result["text"] and "F-bbbbbbbbbb" in result["text"]
    assert result["evidence"] == [{"id": "F-bbbbbbbbbb", "text": CONTEXT["facts"][1]["text"]}]
    assert result["recommended"] == ["NUT-45673"] and result["mode"] == "local"
    assert {"Drug:DRUG-metformin", "Ingredient:NUT-45673", "Doc:DOC-intake-1"} <= set(result["context_keys"])


def test_claim_without_evidence_rejected():
    result, *_ = run(draft(claims=[{"text": "근육경련에는 마그네슘이 연결돼 있습니다.", "evidence_ids": []}]))
    assert result["status"] == "rejected" and result["claims"] == []
    assert "마그네슘" not in result["text"]               # 답변 문장은 숨기고 사유만 보여 준다
    assert any("인용" in w for w in result["warnings"])


def test_unknown_evidence_id_rejected():
    result, *_ = run(draft(claims=[{"text": "근육경련에는 마그네슘이 연결돼 있습니다.", "evidence_ids": ["F-zzzzzzzzzz"]}]))
    assert result["status"] == "rejected" and any("F-zzzzzzzzzz" in w for w in result["warnings"])


def test_unanswerable_returns_no_evidence():
    result, *_ = run(draft(answerable=False, claims=[], recommended_ingredient_ids=[]))
    assert result["status"] == "no_evidence"


def test_empty_context_returns_no_evidence_without_generating():
    empty = {"seeds": [], "facts": [], "docs": [], "ingredient_ids": [], "ingredient_names": {}, "max_seed_score": 0.0}
    result, llm, *_ = run(context=empty)
    assert result["status"] == "no_evidence" and [c["tag"] for c in llm.calls] == ["analyze"]


def test_forbidden_expression_rejected():
    result, *_ = run(draft(claims=[{"text": "마그네슘으로 근육경련을 치료할 수 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}]))
    assert result["status"] == "rejected" and any("금지 표현" in w for w in result["warnings"])


def test_intake_number_allowed_only_when_quoted():
    quoted = draft(claims=[{"text": "마그네슘의 일일 섭취량은 94.5~250 mg입니다.", "evidence_ids": ["DOC-intake-1"]}])
    assert run(quoted)[0]["status"] == "answered"
    other_number = draft(claims=[{"text": "마그네슘은 하루 500mg을 섭취합니다.", "evidence_ids": ["DOC-intake-1"]}])
    assert run(other_number)[0]["status"] == "rejected"
    partial = draft(claims=[{"text": "마그네슘은 하루 50mg을 섭취합니다.", "evidence_ids": ["DOC-intake-1"]}])
    assert run(partial)[0]["status"] == "rejected"         # 250mg의 일부인 50mg은 원문에 있는 숫자가 아니다
    not_intake = draft(claims=[{"text": "마그네슘은 하루 250mg을 섭취합니다.", "evidence_ids": ["DOC-function-1"]}])
    assert run(not_intake)[0]["status"] == "rejected"      # 섭취량 문서를 인용하지 않았다


def test_ingredient_named_in_text_must_be_classified():
    unclassified = draft(claims=[{"text": "메트포르민은 비타민 B12를 고갈시킬 수 있습니다.", "evidence_ids": ["F-aaaaaaaaaa"]}],
                         recommended_ingredient_ids=[])
    result, *_ = run(unclassified)
    assert result["status"] == "rejected" and any("비타민 B12" in w for w in result["warnings"])
    classified = draft(claims=unclassified["claims"], recommended_ingredient_ids=[], cautioned_ingredient_ids=["NUT-45669"])
    assert run(classified)[0]["status"] == "answered"      # '비타민 B12' 안의 '비타민 B1'은 따로 세지 않는다


def test_recommendation_without_patient_adds_notice():
    result, *_ = run()
    assert result["safety_checked"] is False
    assert any("환자 정보가 없어 금기를 확인하지 않았" in w for w in result["warnings"])
    assert "환자 정보가 없어 금기를 확인하지 않았" in result["text"]


def test_no_notice_when_nothing_is_recommended():
    cautioned_only = draft(recommended_ingredient_ids=[], cautioned_ingredient_ids=["NUT-45673"])
    result, *_ = run(cautioned_only)
    assert result["status"] == "answered" and not any("금기를 확인하지 않았" in w for w in result["warnings"])


def test_isolated_seed_answer_says_no_linked_evidence():
    isolated = {"seeds": [{"key": "Symptom:SYM-x", "kind": "Symptom", "name": "혓바늘", "score": 0.03, "isolated": True}],
                "facts": [], "docs": [], "ingredient_ids": [], "ingredient_names": {}, "max_seed_score": 0.03}
    result, llm, *_ = run(context=isolated)
    assert result["status"] == "no_evidence"
    assert "혓바늘" in result["text"] and "연결된 근거가 없" in result["text"]
    assert "효과가 없" not in result["text"] and "안전" not in result["text"]
    assert [c["tag"] for c in llm.calls] == ["analyze"]


def test_mode_auto_follows_route_and_vector_uses_baseline():
    assert run(mode="auto")[0]["mode"] == "local"
    assert run(mode="vector")[0]["mode"] == "vector"
    with pytest.raises(ValueError):
        run(mode="없는방식")


def test_usage_reports_latency():
    result, *_ = run()
    assert set(result["usage"]) == {"tokens_in", "tokens_out", "latency_s"} and result["usage"]["latency_s"] >= 0


def test_guard_check_returns_reasons():
    ok, reasons = guard.check(draft(), CONTEXT)
    assert ok and reasons == []
    ok, reasons = guard.check(draft(claims=[{"text": "완치됩니다.", "evidence_ids": []}]), CONTEXT)
    assert not ok and len(reasons) >= 2


# ---------- 명령줄 출력 ----------

def test_cli_render_shows_status_text_warnings_and_evidence():
    from wcrag import cli
    result, *_ = run()
    shown = cli.render(result)
    assert "answered" in shown and "답변함" in shown
    assert result["text"] in shown
    assert "F-bbbbbbbbbb" in shown and "증상 '근육경련'에는" in shown        # 인용한 근거의 원문
    assert all(w in shown for w in result["warnings"])
    assert "[권함] 마그네슘" in shown                                      # 성분 ID가 아니라 이름을 보여 준다
    assert str(result["usage"]["tokens_in"]) in shown


def test_cli_ask_passes_question_patient_and_mode(tmp_path, capsys):
    from wcrag import cli
    patient_file = tmp_path / "patient.json"
    patient_file.write_text('{"age": 80, "sex": "M"}', encoding="utf-8")
    seen = {}

    def fake_ask(question, patient, mode):
        seen.update(question=question, patient=patient, mode=mode)
        return run()[0]

    cli.main(["ask", "근육경련에 무엇을 권하나", "--patient", str(patient_file), "--mode", "local"], ask_fn=fake_ask)

    assert seen == {"question": "근육경련에 무엇을 권하나", "patient": {"age": 80, "sex": "M"}, "mode": "local"}
    assert "answered" in capsys.readouterr().out


# ---------- 고립 개체는 자기 키로 인용한다 ----------

ISOLATED_SEED = {"key": "Symptom:SYM-polyuria", "kind": "Symptom", "name": "다뇨", "score": 0.6, "isolated": True}


def test_isolated_seed_is_citable_by_its_key():
    context = {**copy.deepcopy(CONTEXT), "seeds": CONTEXT["seeds"] + [ISOLATED_SEED]}
    answer = draft(claims=[{"text": "다뇨는 그래프에 있지만 연결된 근거가 없습니다.", "evidence_ids": ["Symptom:SYM-polyuria"]}],
                   recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
    result, llm, *_ = run(draft=answer, context=context)
    assert result["status"] == "answered"
    assert any(e["id"] == "Symptom:SYM-polyuria" and "연결된" in e["text"] for e in result["evidence"])
    line = next(l for l in llm.calls[-1]["payload"].splitlines() if "다뇨" in l)
    assert line.startswith("Symptom:SYM-polyuria |")           # LLM이 보는 줄 맨 앞이 인용 ID다


def test_connected_seed_is_not_citable():
    answer = draft(claims=[{"text": "메트포르민은 그래프에 있습니다.", "evidence_ids": ["Drug:DRUG-metformin"]}],
                   recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
    result, *_ = run(draft=answer)
    assert result["status"] == "rejected"
    assert any("Drug:DRUG-metformin" in w for w in result["warnings"])


# ---------- 최종 리뷰 수정분 ----------

def cached_embed(texts):
    from wcrag import llm as llm_mod
    stored = llm_mod.cached_embeddings(texts)
    assert all(t in stored for t in texts), "저장된 임베딩이 없습니다(호출하지 않음)"
    return [stored[t] for t in texts]


def test_vector_retriever_accepts_mentions(rag):
    """ask()는 모든 검색기를 mentions= 로 부른다. 기준선도 그 모양이어야 --mode vector가 산다."""
    from wcrag.retrieve import vector_only
    ctx = vector_only.retrieve(rag, "마그네슘의 주의사항은?", cached_embed, mentions=["마그네슘"])
    assert ctx["docs"] and ctx["facts"] == []


def test_ask_vector_mode_runs_with_default_retriever(rag):
    """가짜 검색기가 아닌 기본 검색기 묶음으로 vector 모드가 끝까지 돈다 (LLM은 가짜, 임베딩은 저장본)."""
    llm = FakeLLM(draft={"answerable": False, "claims": [], "recommended_ingredient_ids": [], "cautioned_ingredient_ids": []})
    result = ask(rag, "마그네슘의 주의사항은?", None, "vector", embed=cached_embed, complete=llm)
    assert result["status"] == "no_evidence" and result["mode"] == "vector" and result["context_keys"]


def test_recommendation_without_any_claim_is_rejected():
    result, *_ = run(draft=draft(claims=[]))
    assert result["status"] == "rejected" and any("문장" in w for w in result["warnings"])


def test_recommended_ingredient_must_be_named_in_a_claim():
    answer = draft(claims=[{"text": "근육경련에는 연결된 원료가 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
                   recommended_ingredient_ids=["NUT-45673"])
    result, *_ = run(draft=answer)
    assert result["status"] == "rejected" and any("마그네슘" in w for w in result["warnings"])


def test_isolated_citation_cannot_claim_no_effect_or_safety():
    context = {**copy.deepcopy(CONTEXT), "seeds": CONTEXT["seeds"] + [ISOLATED_SEED]}
    for text in ("다뇨에는 효과가 없습니다.", "다뇨 환자는 안전하게 쓸 수 있습니다.", "다뇨는 문제없습니다.", "다뇨에 써도 괜찮습니다."):
        answer = draft(claims=[{"text": text, "evidence_ids": ["Symptom:SYM-polyuria"]}],
                       recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
        ok, reasons = guard.check(answer, context)
        assert not ok and any("고립" in r for r in reasons), text
    fine = draft(claims=[{"text": "다뇨는 그래프에 있지만 연결된 근거가 없습니다.", "evidence_ids": ["Symptom:SYM-polyuria"]}],
                 recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
    assert guard.check(fine, context) == (True, [])


def test_ingredient_outside_context_named_in_text_is_rejected():
    index = {"오메가-3지방산함유유지": ["Ingredient:IND-2014-36"], "마그네슘": ["Ingredient:NUT-45673"],
             "근육경련": ["Symptom:SYM-cramp"]}
    answer = draft(claims=[{"text": "오메가-3 지방산 함유 유지를 검토할 수 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
                   recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
    ok, reasons = guard.check(answer, CONTEXT, None, name_index=index)
    assert not ok and any("오메가-3 지방산 함유 유지" in r and "문맥 밖" in r for r in reasons)
    assert guard.check(draft(), CONTEXT, None, name_index=index) == (True, [])


def test_same_name_with_several_ids_is_inside_if_any_id_is_in_context():
    """p1 평가 M-10: '인삼'은 Ingredient ID가 3개다. 그중 하나가 문맥에 있으면 문맥 밖 성분이 아니다."""
    index = {"마그네슘": ["Ingredient:NUT-45673", "Ingredient:IND-2019-2", "Ingredient:IND-2021-10"]}
    answer = draft(claims=[{"text": "근육경련에는 마그네슘을 검토할 수 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}])
    assert guard.outside_context("근육경련에는 마그네슘을 검토할 수 있습니다.", CONTEXT, index) == []
    assert guard.check(answer, CONTEXT, None, name_index=index) == (True, [])


def test_ask_rejects_ingredient_outside_context_using_graph_names(rag):
    """ask()는 그래프의 전체 이름 사전으로 문맥 밖 성분 언급을 잡는다."""
    answer = draft(claims=[{"text": "오메가-3 지방산 함유 유지를 검토할 수 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
                   recommended_ingredient_ids=[], cautioned_ingredient_ids=[])
    llm, retriever = FakeLLM(draft=answer), Counter(copy.deepcopy(CONTEXT))
    result = ask(rag, "질문", None, "local", embed=Counter([[0.0]]), complete=llm,
                 retrievers={"local": retriever, "vector": retriever})
    assert result["status"] == "rejected" and any("문맥 밖" in w for w in result["warnings"])


def test_forbidden_ingredient_written_favorably_is_blocked_even_if_cautioned():
    verdicts = {"by_ingredient": {"NUT-45673": {"allowed": False, "caution": [],
                                                "avoid": [{"level": "avoid", "by": "와파린", "text": "출혈 위험"}]}},
                "incomplete": []}
    favorable = draft(claims=[{"text": "근육경련에는 마그네슘을 검토할 수 있습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
                      recommended_ingredient_ids=[], cautioned_ingredient_ids=["NUT-45673"])
    assert guard.blocked(favorable, CONTEXT, verdicts)
    ok, reasons = guard.check(favorable, CONTEXT, verdicts)
    assert not ok and any("피하라는 말 없이" in r for r in reasons)
    warned = draft(claims=[{"text": "마그네슘은 와파린과 상호작용하므로 권하지 않습니다.", "evidence_ids": ["F-bbbbbbbbbb"]}],
                   recommended_ingredient_ids=[], cautioned_ingredient_ids=["NUT-45673"])
    assert guard.blocked(warned, CONTEXT, verdicts) == [] and guard.check(warned, CONTEXT, verdicts) == (True, [])
