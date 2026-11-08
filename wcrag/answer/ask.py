"""질의응답 진입점. 질문 분석 → 검색 → 금기 판정 → 답변 생성 → 검증을 잇는다.

결과 status
  answered    : 검증을 통과한 답변
  no_evidence : 답할 근거가 없음
  stopped     : 위험 신호라 검색·답변 없이 멈춤
  rejected    : 답변이 검증에 걸려 숨김 (사유는 warnings)
  blocked     : 이 환자에게 금기인 성분을 권해서 막음 (사유는 text와 warnings)
  fallback    : 답할 근거가 없어서 나이·성별 구분의 관심사 기준 일반 추천을 붙임 (LLM 없이 규칙, 금기 판정함)
                no_evidence가 날 자리에서 나이·성별을 알 때만 낸다. rejected·blocked 자리는 채우지 않는다
"""
import time

from .. import config, llm
from . import fallback, guard, safety_tool
from .analyze import analyze
from .generate import generate, kind_name

MODES = ("auto", "vector", "local")
STOPPED_TEXT = "위험 신호로 판단해 답변을 중단했습니다. 건강기능식품을 권하기 전에 진료를 먼저 권유하세요. 사유: {reason}"
NO_EVIDENCE_TEXT = "그래프와 문서에서 이 질문에 답할 근거를 찾지 못했습니다."
ISOLATED_TEXT = ("'{names}'은(는) 그래프에 있지만 다른 대상과 이어진 관계나 문서가 없습니다. "
                 "관련 성분이 없다는 뜻이 아니라, 지금 그래프에 연결된 근거가 없다는 뜻입니다.")
REJECTED_TEXT = "답변이 검증을 통과하지 못해 표시하지 않습니다. 사유를 확인하세요."
ISOLATED_EVIDENCE = "'{name}'({kind})은(는) 그래프에 있지만 연결된 관계나 문서가 없음"
BLOCKED_TEXT = "이 환자에게 금기인 성분을 권하는 답변이어서 막았습니다. {reasons}"
NO_PATIENT_NOTICE = "환자 정보가 없어 금기를 확인하지 않았습니다. 권하기 전에 복용약과 질환을 확인하세요."
UNCHECKED_NOTICE = "환자의 나이나 성별이 없어 금기를 판정하지 못했습니다. 권하기 전에 복용약과 질환의 금기를 직접 확인하세요."
INCOMPLETE_NOTICE = "금기 판정이 불완전합니다. 경고에 적힌 항목은 확인되지 않았습니다."
FALLBACK_TEXT = ("{lead} 대신 '{segment}' 구분의 주요 관심사 기준 성분을 안내합니다. "
                 "질문에 대한 답이나 개인 맞춤 추천이 아니라 같은 성별·연령대의 일반 추천이며, 금기 판정은 거쳤습니다: {items}")
FALLBACK_BLOCKED = "관심사 기준 후보에서 금기로 뺀 성분: {name} — {reasons}"
FALLBACK_EVIDENCE = "'{segment}' 구분의 관심사 경로: {paths}"


def default_retrievers() -> dict:
    from ..retrieve import local, vector_only
    return {"local": local.retrieve, "vector": vector_only.retrieve}


def graph_names(graph) -> dict | None:
    """그래프 전체의 개체 이름 사전(접속마다 한 번 읽음). 그래프가 없는 테스트에서는 None."""
    if graph is None:
        return None
    from ..retrieve import local
    return local.name_index(graph)


def context_keys(context: dict) -> list[str]:
    """문맥에 든 개체 키와 문서 ID. 평가의 검색 적중이 이 값을 본다."""
    keys = [s["key"] for s in context["seeds"]]
    for fact in context["facts"]:
        keys += [k for k in (fact.get("start"), fact.get("end")) if k]
    keys += [f"Ingredient:{i}" for i in context["ingredient_ids"]]
    keys += [f"Doc:{d['id']}" for d in context["docs"]]
    return list(dict.fromkeys(keys))


def answer_text(claims: list[dict]) -> str:
    return " ".join(f"{c['text']} [{', '.join(c['evidence_ids'])}]" for c in claims)


def caution_notes(recommended: list[str], context: dict, verdicts: dict | None) -> list[str]:
    """권한 성분에 '주의' 판정이 있으면 그 문구를 그대로 붙인다. 주의는 막지 않고 알리기만 한다."""
    by_ingredient = (verdicts or {}).get("by_ingredient", {})
    return [f"주의 필요: {context['ingredient_names'].get(iid, iid)} — {finding['by']}: {finding['text']}"
            for iid in recommended for finding in by_ingredient.get(iid, {}).get("caution", [])]


def ask(graph, question: str, patient: dict | None = None, mode: str = "auto", *, embed, complete,
        retrievers: dict | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"알 수 없는 검색 방식: {mode!r}. {', '.join(MODES)} 중 하나여야 합니다.")
    started, usage_from = time.time(), len(llm.USAGE_LOG)
    result = {"status": "", "text": "", "claims": [], "recommended": [], "cautioned": [], "evidence": [],
              "context_keys": [], "safety_checked": False, "safety_incomplete": [], "warnings": [], "mode": mode,
              "usage": {}}

    def done(status: str, text: str) -> dict:
        calls = llm.USAGE_LOG[usage_from:]
        result.update(status=status, text=text, usage={
            "tokens_in": sum(c["tokens_in"] for c in calls), "tokens_out": sum(c["tokens_out"] for c in calls),
            "latency_s": round(time.time() - started, 3)})
        return result

    def no_evidence(text: str) -> dict:
        """근거가 없을 때. 나이·성별을 알면 관심사 기준 일반 추천을 붙이고, 모르면 그대로 no_evidence."""
        rec = fallback.recommend(graph, person)
        if not rec or not rec["picked"]:
            return done("no_evidence", text)
        segment = rec["segment"]["name"]
        picked = rec["picked"]
        result.update(
            recommended=[p["ingredient_id"] for p in picked], safety_checked=True, safety_incomplete=rec["incomplete"],
            ingredient_names={p["ingredient_id"]: p["name"] for p in picked},
            evidence=[{"id": f"Ingredient:{p['ingredient_id']}",
                       "text": FALLBACK_EVIDENCE.format(segment=segment, paths=" / ".join(p["paths"]))} for p in picked])
        result["warnings"] += [f"금기 판정 불완전 — {reason}" for reason in rec["incomplete"]]
        result["warnings"] += [f"주의 필요: {p['name']} — {f['by']}: {f['text']}" for p in picked for f in p["cautions"]]
        result["warnings"] += [FALLBACK_BLOCKED.format(name=b["name"], reasons="; ".join(
            f"{f['by']}: {f['text']}" for f in b["reasons"])) for b in rec["blocked"]]
        items = ", ".join(p["name"] for p in picked)
        return done("fallback", FALLBACK_TEXT.format(lead=text, segment=segment, items=items))

    # 1. 질문 분석 — 위험 신호면 여기서 끝난다
    route = analyze(question, complete, complaint=(patient or {}).get("complaint"))
    if route["red_flag"]:
        return done("stopped", STOPPED_TEXT.format(reason=route["red_flag_reason"]))
    # 신원(나이·성별·임신)은 넘겨받은 환자 정보가 먼저, 약·질환은 질문 단서와 합친다
    person = safety_tool.merge_patient(patient, route["patient"])

    # 2. 검색
    used = route["modes"][0] if mode == "auto" else mode
    result["mode"] = used
    retrieve = (retrievers or default_retrievers())[used]
    context = retrieve(graph, question, embed, mentions=route.get("mentions") or [])
    result["context_keys"] = context_keys(context)

    if not context["facts"] and not context["docs"]:
        isolated = [f"{s['name']}({kind_name(s['kind'])})" for s in context["seeds"] if s["isolated"]]
        return no_evidence(ISOLATED_TEXT.format(names=", ".join(isolated)) if isolated else NO_EVIDENCE_TEXT)
    if config.MIN_SCORE is not None and context["max_seed_score"] < config.MIN_SCORE:
        return no_evidence(NO_EVIDENCE_TEXT)

    # 3. 금기 판정
    verdicts, notice = None, None
    if person is None:
        notice = NO_PATIENT_NOTICE
    elif not safety_tool.usable(person):
        notice = UNCHECKED_NOTICE
        result["warnings"].append(UNCHECKED_NOTICE)
    else:
        verdicts = safety_tool.verdicts(graph, person, context["ingredient_ids"])
        result.update(safety_checked=True, safety_incomplete=verdicts["incomplete"])
        result["warnings"] += [f"금기 판정 불완전 — {reason}" for reason in verdicts["incomplete"]]
        notice = INCOMPLETE_NOTICE if verdicts["incomplete"] else None

    # 4. 답변 생성
    draft = generate(question, context, complete, verdicts)
    if not draft["answerable"]:
        return no_evidence(NO_EVIDENCE_TEXT)

    # 5. 검증 — 금기 성분을 권했으면 blocked, 그 밖의 문제는 rejected. 그래프가 있으면 전체 이름 사전으로 문맥 밖 성분도 잡는다
    name_index = graph_names(graph)
    ok, reasons = guard.check(draft, context, verdicts, name_index=name_index)
    if not ok:
        result["warnings"] += reasons
        veto = guard.blocked(draft, context, verdicts)
        return done("blocked", BLOCKED_TEXT.format(reasons=" / ".join(veto))) if veto else done("rejected", REJECTED_TEXT)

    evidence = {**{f["id"]: f["text"] for f in context["facts"]}, **{d["id"]: d["text"] for d in context["docs"]},
                **{s["key"]: ISOLATED_EVIDENCE.format(name=s["name"], kind=kind_name(s["kind"]))
                   for s in context["seeds"] if s["isolated"]}}
    cited = list(dict.fromkeys(e for c in draft["claims"] for e in c["evidence_ids"]))
    result.update(claims=draft["claims"], recommended=draft["recommended_ingredient_ids"],
                  cautioned=draft["cautioned_ingredient_ids"], evidence=[{"id": e, "text": evidence[e]} for e in cited],
                  ingredient_names={i: context["ingredient_names"].get(i, i)
                                    for i in draft["recommended_ingredient_ids"] + draft["cautioned_ingredient_ids"]})
    text = answer_text(draft["claims"])
    if result["recommended"]:
        result["warnings"] += caution_notes(result["recommended"], context, verdicts)
        if notice:
            text = f"{text} ※ {notice}"
            if notice == NO_PATIENT_NOTICE:
                result["warnings"].append(NO_PATIENT_NOTICE)
    return done("answered", text)
