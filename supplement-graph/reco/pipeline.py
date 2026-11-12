"""추천 파이프라인.

    1. LLM      주호소 정규화, 응급·진료 필요 판정 (emergency 진료 요망 · visit 진료 권고. 둘 다 추천은 이어간다)
    2. GraphDB  2-1 증상 후보 (사용자 증상 + 생애주기 증상 참고)   2-2 관심사 후보 (나이·성별)
                2-3 복용약 후보 (식약처 허가사항에 결핍·보충 문장이 있는 약 → 영양소)
    3. 조합     같은 원료는 하나로 합치고 기준마다 1점: 증상 1점, 복용약 1점(A 등급만), 관심사 1점
                동점이면 증상 > 복용약 > 관심사, 그다음 생애주기 우선 증상(그 나이·성별의 다빈도 질환 증상과
                겹치는 사용자 증상)을 직접 경로로 덮는 수, 그다음 직접 경로로 걸린 증상 수
    4. 검증     4-1 금지 성분 제외   4-2 주의 문구 · 시너지   4-3 국내 공공기관 출처가 있는 것만
    5. LLM      점수순 TOP-10 중 3개 선정

어떤 성분을 후보로 올릴지는 서버(이 파일)가 그래프 조회로 정한다. LLM 은 후보 밖 성분을 고를 수 없다.
"""
import re

TOP_N = 10
PICKS = 3

# 4-3 에서 인정하는 국내 공공기관
PUBLIC_ORGS = ("식약처", "식품의약품안전처", "질병관리청", "국민건강보험공단", "건강보험심사평가원",
               "보건복지부", "국립정신건강센터", "국가정신건강정보포털")

# 성별에 맞지 않는 경로. 남성에게 여성호르몬 기전·여성 건강 기능성, 여성에게 전립선 기능성을 쓰지 않는다
SKIP_BY_SEX = {
    "M": {"mechanisms": ["M49", "M51", "M52"], "tags": ["T13", "T15"]},
    "F": {"mechanisms": [], "tags": ["T17"]},
}

# 복용약 계열 표기 → 그래프의 약물 계열
DRUG_CLASS_KEYWORDS = [
    ("CLS-anticoagulant", r"항응고|항혈소판|항혈전|와파린|아스피린"),
    ("CLS-antidiabetic", r"당뇨약|혈당강하"),
    ("CLS-antihypertensive", r"혈압약|혈압강하"),
    ("CLS-antidepressant", r"항우울"),
    ("CLS-immunosuppressant", r"면역억제"),
    ("CLS-hormone", r"호르몬"),
    ("CLS-sedative", r"수면제|신경안정|진정제"),
]
# 질환 표기 → 그래프의 질환 조건
DISEASE_KEYWORDS = [
    ("DIS-diabetes", r"당뇨"), ("DIS-hypertension", r"고혈압"),
    ("DIS-kidney", r"신장|신부전"), ("DIS-liver", r"간질환|간 질환|간기능|지방간|간염"),
    ("DIS-gi", r"위염|위궤양|십이지장궤양|역류성\s?식도염|위식도역류|장염|크론|궤양성\s?대장염"),
    ("DIS-heart", r"심장|부정맥|협심증|심근경색|심혈관|심부전"),
    ("DIS-dyslipidemia", r"이상지질혈증|고지혈증|고콜레스테롤|콜레스테롤"),
    ("DIS-thyroid", r"갑상선"),
]
# 직접 경로 = 원본 엑셀(경로 시트)에서 두 구간이 모두 확인된 경로. 기능성→기전 판정이 '일치'(SUPPORTED_BY confirmed)이고
# 기전→증상 판정이 '근거 있음'(EXPLAINED_BY confirmed)인 경로다. 2-1 쿼리의 confirmed 가 이 값이다
def is_direct(p):
    return bool(p["confirmed"])


# 같은 원료군: 한 사람에게 둘 이상 고르지 않는다(사실상 같은 원료). 이름에 정규식을 건다. 약사 검토 전 초안이다
SAME_FAMILY = [("인삼", r"인삼|홍삼"), ("글루코사민", r"글루코사민"), ("오메가-3", r"EPA|DHA|오메가-?3")]


def family(c):
    return next((f for f, pat in SAME_FAMILY if re.search(pat, c["name"])), None)


def independent_symptoms(paths):
    """직접 경로로 덮는 사용자 증상 수. 단 기전 하나로 설명되는 증상들은 하나로 센다(허브 기전 할인).
    증상과 기전의 최대 짝짓기 크기다. 예: 갱년기·생리주기·예민함이 모두 '여성호르몬 변화' 하나로만 이어지면 1."""
    mechs = {}
    for p in paths:
        mechs.setdefault(p["symptom_id"], set()).add(p["mechanism"])
    match = {}

    def try_(s, seen):
        for m in mechs[s]:
            if m not in seen:
                seen.add(m)
                if m not in match or try_(match[m], seen):
                    match[m] = s
                    return True
        return False
    return sum(try_(s, set()) for s in mechs)


ROUTES = ("증상", "복용약", "관심사")          # 동점일 때 앞선 기준이 있는 후보가 먼저다
CARE_LABEL = {"emergency": "진료 요망", "visit": "진료 권고"}     # 1-2 판정을 화면에 보여줄 때 쓰는 말
KIND_ORDER = {"Nutrient": 0, "Notice": 1, "Individual": 2}
KIND_LABEL = {"Nutrient": "영양성분", "Notice": "고시형", "Individual": "개별인정형"}


def is_public(source):
    return bool(source) and any(org in source for org in PUBLIC_ORGS)


def patient_view(p):
    """LLM 에 보내는 사용자 정보. 식별자는 뺀다."""
    checkup = [f"{i['item']} {i['value']}{i.get('unit') or ''} ({i['status']})"
               for i in (p.get("checkup") or {}).get("items", []) if i.get("status") != "정상"]
    return {"age": p["age"], "sex": p["sex"],
            "chief_complaint": (p.get("chief_complaint") or {}).get("text"),
            "managed_conditions": p.get("managed_conditions") or [],
            "comorbidities": p.get("comorbidities") or [],
            "abnormal_checkup": checkup,
            "medications": [f"{m['name']} ({m.get('class')}" + (f", {m['change']}" if m.get("change") else "") + ")"
                            for m in p.get("medications") or []]}


def safety_inputs(p, matchers=()):
    """사용자 데이터 → 안전 필터·복용약 후보 파라미터. 그래프에는 개인 데이터를 넣지 않고 파라미터로만 넘긴다.

    matchers 는 그래프의 약 성분·계열 정규식(drug_matchers)이다. 약 이름과 분류 글자에 건다.
    """
    codes = set()
    if p["age"] < 3:
        codes.add("POP-infant")
    if p["age"] < 19:
        codes.add("POP-child")
    diseases = list(p.get("managed_conditions") or []) + list(p.get("comorbidities") or [])
    diseases += [i["category"] for i in (p.get("checkup") or {}).get("items", []) if i.get("status") == "질환의심"]
    for code, pat in DISEASE_KEYWORDS:
        if any(re.search(pat, d) for d in diseases):
            codes.add(code)
    drugs = set()
    meds = p.get("medications") or []
    for m in meds:
        text = f"{m.get('name', '')} {m.get('class', '')}"
        drugs.update(d for d, pat in DRUG_CLASS_KEYWORDS if re.search(pat, text))
        drugs.update(m["drug_id"] for m in matchers if re.search(m["match"], text))
    if meds:
        drugs.add("CLS-any")
    return sorted(codes), sorted(drugs)


def symptom_candidates(graph, symptom_ids, lifecycle_ids, sex):
    """2-1. 사용자 증상에서 출발한 후보. 4-3 을 위해 구간마다 공공 출처가 있는 경로만 근거로 남긴다."""
    skip = SKIP_BY_SEX[sex]
    rows = graph.run("symptom_candidates", symptom_ids=symptom_ids, lifecycle_ids=lifecycle_ids,
                     skip_mechanisms=skip["mechanisms"], skip_tags=skip["tags"])
    out = {}
    for r in rows:
        paths = [p for p in r["paths"] if all(is_public(s) for s in p["sources"])]
        if not paths:
            continue
        covered = {p["symptom_id"] for p in paths}
        out[r["ingredient_id"]] = {
            "ingredient_id": r["ingredient_id"], "name": r["name"], "kind": r["kind"], "paths": paths,
            "n_symptoms": len(covered),
            "n_lifecycle": len({p["symptom_id"] for p in paths if p["lifecycle"]}),
            "n_confirmed": len({p["symptom_id"] for p in paths if p["confirmed"]})}
    return out


def interest_candidates(graph, age, sex):
    """2-2. 나이·성별의 관심사에서 출발한 후보."""
    out = {}
    for r in graph.run("interest_candidates", age=age, sex=sex):
        out[r["ingredient_id"]] = {
            "ingredient_id": r["ingredient_id"], "name": r["name"], "kind": r["kind"], "segment": r["segment"],
            "interests": r["interests"], "function_text": r["function_text"],
            "interest_order": min((i["interest_order"], i["order"]) for i in r["interests"])}
    return out


def drug_candidates(graph, drug_ids):
    """2-3. 복용약에서 출발한 후보. A 등급(결핍과 함께 보충·모니터링 권고)만 후보가 되고,
    B 등급(결핍이 이상반응으로만 적힘)은 다른 기준으로 올라온 후보에 붙이는 참고 표시로 따로 돌려준다."""
    out, notes = {}, {}
    for r in graph.run("drug_candidates", drug_ids=drug_ids) if drug_ids else []:
        deps = [d for d in r["depletions"] if is_public(d["source"])]
        a = [d for d in deps if d["grade"] == "A"]
        b = [d for d in deps if d["grade"] != "A"]
        if a:
            out[r["ingredient_id"]] = {"ingredient_id": r["ingredient_id"], "name": r["name"], "kind": r["kind"],
                                       "function_text": r["function_text"], "depletions": a}
        if b:
            notes[r["ingredient_id"]] = b
    return out, notes


def combine(by_symptom, by_interest, by_drug=None, drug_notes=None):
    """3. 같은 원료를 하나로 합친다. 점수는 증상 1점(직접 경로가 있을 때만), 복용약 1점, 관심사 1점,
    생애주기 우선 증상을 직접 덮으면 1점(최대 4점).
    순서는 직접 경로로 덮는 사용자 증상 수(n_direct, 기전 하나로 설명되는 증상은 하나로 셈)가 가장 먼저다.
    그 수가 같으면 점수, 증상 > 복용약 > 관심사 기준이 있는 쪽, 생애주기 우선 증상을 직접 덮는 수,
    근거가 많은 순.

    생애주기 우선 증상 = 사용자 증상 중 그 나이·성별의 다빈도 질환 증상과 겹치는 것(2-1 의 lifecycle 표시)."""
    by_drug, drug_notes = by_drug or {}, drug_notes or {}
    merged = []
    for iid in set(by_symptom) | set(by_interest) | set(by_drug):
        s, i, d = by_symptom.get(iid), by_interest.get(iid), by_drug.get(iid)
        c = {**(d or {}), **(i or {}), **(s or {})}
        c["interests"] = (i or {}).get("interests", [])
        c["paths"] = (s or {}).get("paths", [])
        c["depletions"] = (d or {}).get("depletions", [])
        c["drug_notes"] = drug_notes.get(iid, [])
        direct = [p for p in c["paths"] if is_direct(p)]
        c["n_direct"] = independent_symptoms(direct)
        c["direct_symptoms"] = sorted({p["symptom_id"] for p in direct})
        c["n_priority"] = len({p["symptom_id"] for p in direct if p["lifecycle"]})
        # 증상 기준은 직접 경로가 하나라도 있을 때만 걸린 것으로 본다. 간접 경로뿐이면 경로는 남기되 점수는 주지 않는다
        c["routes"] = [name for name, hit in zip(ROUTES, (c["n_direct"], d, i)) if hit]
        c["priority_bonus"] = 1 if c["n_priority"] else 0      # 생애주기 우선 증상을 직접 덮으면 +1
        c["score"] = len(c["routes"]) + c["priority_bonus"]
        merged.append(c)

    return sorted(merged, key=lambda c: rank_key(c) + (KIND_ORDER.get(c["kind"], 9), c["ingredient_id"]))


def rank_key(c):
    """근거로 정하는 순서. 이 값이 같은 후보끼리는 근거로 가를 수 없다."""
    return (-c["n_direct"], -c["score"], [name not in c["routes"] for name in ROUTES], -c["n_priority"],
            -c.get("n_symptoms", 0), -c.get("n_lifecycle", 0), -c.get("n_confirmed", 0),
            c.get("interest_order", (99, 99)))


def verify(graph, candidates, codes, drugs):
    """4. 금지 성분을 빼고(4-1), 주의 문구를 붙이고(4-2), 공공 출처가 있는 것만 남긴다(4-3)."""
    if not candidates:
        return [], []
    blocks = {r["ingredient_id"]: r["blocks"] for r in graph.run(
        "safety_filter", ingredient_ids=[c["ingredient_id"] for c in candidates],
        condition_codes=codes, drug_ids=drugs)}
    kept, dropped = [], []
    for c in candidates:
        if c["ingredient_id"] not in blocks:           # 안전 필터가 답하지 않은 성분은 통과시키지 않는다
            dropped.append({**_brief(c), "why": "안전 필터 결과 없음"})
            continue
        hits = blocks[c["ingredient_id"]]
        avoid = [b for b in hits if b.get("severity") == "avoid"]
        if avoid:
            dropped.append({**_brief(c), "why": "금지: " + "; ".join(b.get("reason") or b.get("note") or "" for b in avoid)})
            continue
        c["cautions"] = sorted({b.get("reason") or b.get("note") for b in hits if b.get("reason") or b.get("note")})
        # 4-3: 증상 후보는 경로 출처(2-1), 복용약 후보는 허가사항 문장(2-3)이 근거다. 관심사만 있는 후보는 식약처 기능성 문구가 근거다
        if c["routes"] == ["관심사"] and not c.get("function_text"):
            dropped.append({**_brief(c), "why": "공공 출처 없음"})
            continue
        kept.append(c)
    return kept, dropped


def _brief(c):
    return {"ingredient_id": c["ingredient_id"], "name": c["name"], "score": c["score"], "routes": c["routes"]}


def evidence_key(c):
    """이 사용자에게 걸린 근거(기준, 증상 → 기전 → 기능성 경로, 관심사만이면 관심사, 복용약만이면 약)."""
    return (tuple(c["routes"]), frozenset((p["symptom_id"], p["mechanism"], p["function"]) for p in c["paths"]),
            frozenset(i["interest"] for i in c["interests"]) if c["routes"] == ["관심사"] else None,
            frozenset(d["drug_id"] for d in c["depletions"]) if not c["paths"] else None)


def _completeness(c, basics):
    """근거가 같은 원료 중 대표를 고르는 순서: 영양성분·고시형(식약처 공통 기준) > 개별인정형(업체별 인정),
    그다음 일일섭취량 원문이 있는 것, 주의사항 원문이 있는 것. 마지막의 ID 는 근거와 무관한 정렬이다."""
    b = basics.get(c["ingredient_id"], {})
    intake = b.get("intake_raw")
    return (KIND_ORDER.get(c["kind"], 9), not (intake and "누락" not in intake), not b.get("precaution_text"),
            c["ingredient_id"])


def top_n(candidates, n=TOP_N, synergy=None, basics=None, min_n=PICKS):
    """근거 묶음 단위로 n 개. 근거(evidence_key)가 같은 원료는 한 묶음으로 보고 대표 하나만 자리를 갖는다.
    나머지는 대표의 alternatives 로 붙어 화면에 '같은 근거 원료' 로 보인다(버리지 않는다).
    - 같은 이름의 원료(업체별 개별인정 등)는 앞선 하나만 남긴다
    - 묶음 안에 대표와 시너지 짝인 원료가 있으면 그 원료도 한 자리를 갖는다(칼슘·비타민 D 등)
    - 묶음 순서는 rank_key, 그 값이 같으면 대표의 _completeness 로 정한다
    - 묶음이 min_n 개보다 적으면 묶음의 다음 원료로 min_n 개까지 채운다"""
    synergy, basics = synergy or {}, basics or {}
    names, groups = set(), {}
    for c in candidates:
        name = re.sub(r"\s+", "", c["name"])
        if name in names:
            continue
        names.add(name)
        groups.setdefault(evidence_key(c), []).append(c)
    reps = []
    for members in groups.values():
        members.sort(key=lambda c: _completeness(c, basics))
        reps.append(members)
    reps.sort(key=lambda m: (min(rank_key(c) for c in m), _completeness(m[0], basics)))
    out = []
    for members in reps:
        rep = members[0]
        partner = next((m for m in members[1:] if m["ingredient_id"] in synergy.get(rep["ingredient_id"], ())), None)
        rest = [m for m in members[1:] if m is not partner]
        rep["alternatives"] = [{"ingredient_id": m["ingredient_id"], "name": m["name"], "kind": m["kind"]} for m in rest]
        rep["_rest"] = rest
        for m in members:                         # 묶음의 순서 = 묶음 안에서 가장 앞서는 원료의 순서
            m["group_key"] = min(rank_key(x) for x in members)
        out.append(rep)
        if partner:
            partner["alternatives"], partner["_rest"], partner["synergy_with"] = [], [], rep["name"]
            out.append(partner)
        if len(out) >= n:
            break
    out = out[:n]
    while len(out) < min_n:                       # 묶음이 모자라면 묶음의 다음 원료로 채운다
        extra = next((c for c in out if c.get("_rest")), None)
        if not extra:
            break
        m = extra["_rest"].pop(0)
        extra["alternatives"] = [a for a in extra["alternatives"] if a["ingredient_id"] != m["ingredient_id"]]
        m["alternatives"], m["_rest"], m["same_evidence_as"] = [], [], extra["name"]
        out.append(m)
    for c in out:
        c.pop("_rest", None)
    return out


def conflict(x, y):
    """함께 고르지 않는 쌍: 흡수·작용을 방해하는 관계(길항)이거나 같은 원료군."""
    return (any({a["a_id"], a["b_id"]} == {x["ingredient_id"], y["ingredient_id"]} for a in x.get("antagonists", []))
            or (family(x) is not None and family(x) == family(y)))


def conflict_why(c, other):
    if family(c) is not None and family(c) == family(other):
        return f"이미 고른 {other['name']}와 같은 원료군({family(c)})이라 함께 고르지 않았습니다"
    return f"이미 고른 {other['name']}와 흡수·작용을 방해하는 관계라 함께 고르지 않았습니다"


def symptom_slots(ranked, symptom_ids, want=PICKS):
    """5-1. 사용자 증상마다 한 자리. 증상 순서(주호소에 적힌 순)대로, 아직 덮이지 않은 증상을 직접 덮는 후보 중
    순서가 가장 앞선 원료를 고른다. 이미 고른 원료와 충돌하면 건너뛴다. 덮을 원료가 없는 증상은 비워 둔다."""
    slots, covered, missing = [], set(), []
    for sid in symptom_ids:
        if len(slots) >= want or sid in covered:
            continue
        c = next((c for c in ranked if sid in c.get("direct_symptoms", ()) and c not in slots
                  and not any(conflict(c, o) for o in slots)), None)
        if c is None:
            missing.append(sid)
            continue
        slots.append(c)
        covered |= set(c.get("direct_symptoms", ()))
    return slots, missing


def split_fixed(top, want=PICKS):
    """5-1. top 을 확정 후보와 동점 후보(pool)로 나눈다. 순서(묶음 단위 rank_key)가 want 번째로 앞서는 값보다
    앞서면 확정, 같으면 동점 후보다. 확정 후보끼리 길항이면 뒤 순위를 동점 후보로 내린다."""
    clash = conflict
    key = lambda c: c.get("group_key") or rank_key(c)  # noqa: E731
    cut = sorted(map(key, top))[want - 1]          # want 번째로 앞서는 순서. 같은 근거로 채운 원료는 표 위치와 순서가 다를 수 있다
    rule_why, fixed, demoted = {}, [], []
    for c in (c for c in top if key(c) < cut):
        hit = next((f for f in fixed if clash(c, f)), None)
        if hit:
            demoted.append(c)
            rule_why[c["ingredient_id"]] = conflict_why(c, hit)
        else:
            fixed.append(c)
    pool = [c for c in top if c in demoted or (key(c) == cut and c not in fixed)]
    return fixed, pool, rule_why


def detail(pack):
    """영양제 탭에 보여줄 네 항목. 전부 식약처 원문 그대로이고, 없는 값은 None 으로 둔다(지어내지 않는다)."""
    intake = pack.get("intake_raw")
    if intake and "누락" in intake:      # 원본의 '일일섭취량 누락' 같은 자리 표시는 값이 아니다
        intake = None
    if intake and pack.get("intake_unit") and not re.search(r"[A-Za-z가-힣μ]", intake):
        intake = f"{intake} {pack['intake_unit']}/일"          # API 행은 숫자와 단위가 따로 온다
    if pack.get("raw_materials"):
        raw, basis = pack["raw_materials"], "고시 원재료"
    elif pack.get("manufacturing_std") and not re.search(r"자료 누락|정보 없음", pack["manufacturing_std"]):
        raw, basis = pack["manufacturing_std"], "영양성분 제조기준(원료)"
    elif pack.get("kind") == "Individual":   # 개별인정형은 원재료 자료가 따로 없어 인정받은 원료명을 그대로 쓴다
        raw, basis = pack.get("name_raw") or pack.get("name"), "개별인정 원료명(원재료 자료 없음)"
    else:
        raw, basis = None, "원본에 자료 없음"
    return {"daily_intake": intake, "raw_material": raw, "raw_material_basis": basis,
            "precautions": pack.get("precaution"), "main_function": pack.get("function_text"),
            "source": pack.get("source")}


def enrich(graph, top, kept_ids):
    """근거 묶음(섭취량 원문 등)과 4-2 시너지·길항을 붙인다. 시너지 짝은 안전 검증을 통과한 성분만."""
    ids = [c["ingredient_id"] for c in top]
    packs = {r["ingredient_id"]: r for r in graph.run("evidence_pack", ingredient_ids=ids)}
    partners = {}
    for r in graph.run("synergy_partners", ingredient_ids=ids):
        if is_public(r["source"]):
            partners.setdefault(r["ingredient_id"], []).append(r)
    antagonists = graph.run("antagonist_pairs", ingredient_ids=ids)
    for c in top:
        pack = packs.get(c["ingredient_id"], {})
        c["intake"] = pack.get("intake_raw")
        c["functions"] = [f["tag"] for f in pack.get("functions", [])]
        c["precaution_text"] = pack.get("precaution")
        c["detail"] = detail(pack)
        c["synergy"] = [dict(p, in_candidates=p["partner_id"] in ids, passed_safety=p["partner_id"] in kept_ids)
                        for p in partners.get(c["ingredient_id"], [])]
        c["antagonists"] = [a for a in antagonists if c["ingredient_id"] in (a["a_id"], a["b_id"])]
    return top


def _drug_line(d):
    """복용약 근거 한 줄: 약 이름, 허가사항이 말하는 것, 원문 문장."""
    return f"{d['drug']} 복용 → {d['effect']} (식약처 허가사항: \"{d['quote']}\")"


def llm_view(c, rank):
    """5단계 LLM 에 넘기는 후보 한 건. 서버가 정한 근거만 담는다."""
    reasons = sorted({f"{p['symptom']} ← {p['mechanism']}" + (f" ← {p['function']}" if p["function"] else " (결핍 보충)")
                      for p in c["paths"]})
    return {"rank": rank, "n_symptoms_direct": c["n_direct"], "score": c["score"], "routes": c["routes"],
            "ingredient_id": c["ingredient_id"], "name": c["name"],
            "kind": KIND_LABEL.get(c["kind"], c["kind"]), "functions": c.get("functions", []),
            "symptom_paths": reasons[:8],
            "priority_symptoms": sorted({p["symptom"] for p in c["paths"] if p["lifecycle"]
                                         and is_direct(p)}),
            "drug_basis": [_drug_line(d) for d in c["depletions"]],
            "drug_notes": [_drug_line(d) for d in c["drug_notes"]],
            "interests": sorted({i["interest"] for i in c["interests"]}),
            "intake": c.get("intake"), "cautions": c.get("cautions", []),
            "synergy": [f"{s['partner']}: {s['effect']}" + (" (후보에 있음)" if s["in_candidates"] else "")
                        for s in c.get("synergy", []) if s["passed_safety"]],
            "antagonists": [f"{a['a_name']} ↔ {a['b_name']}: {a['effect']}" for a in c.get("antagonists", [])]}


def normalize(llm, view, symptom_list):
    """1. LLM 정규화 결과를 서버가 한 번 더 거른다: 목록에 없는 id 는 버리고, 같은 증상은 하나로 합친다."""
    names = {s["symptom_id"]: s["name"] for s in symptom_list}
    norm = llm.normalize(view, symptom_list)
    seen, symptoms = set(), []
    for s in norm["symptoms"]:
        if s["symptom_id"] in names and s["symptom_id"] not in seen:
            seen.add(s["symptom_id"])
            symptoms.append({**s, "name": names[s["symptom_id"]]})
    return {**norm, "symptoms": symptoms, "context": norm.get("context", []),
            "care_symptom_ids": [i for i in norm.get("care_symptom_ids", []) if i in seen]}


def recommend(patient, graph, llm, symptom_list):
    view = patient_view(patient)
    result = {"patient_id": patient["patient_id"], "age": patient["age"], "sex": patient["sex"],
              "chief_complaint": view["chief_complaint"]}

    # 1. 정규화 · 진료 필요 판정
    names = {s["symptom_id"]: s["name"] for s in symptom_list}
    norm = normalize(llm, view, symptom_list)
    symptoms = norm["symptoms"]
    result["step1"] = {"symptoms": symptoms, "unmatched": norm["unmatched"], "context": norm["context"],
                       "care_level": norm["care_level"], "care_reason": norm["care_reason"]}
    # emergency 는 '진료 요망', visit 는 '진료 권고' 를 먼저 낸다. 둘 다 진료 사유가 된 증상을 뺀 나머지로 추천을 이어간다
    # (추천은 참고용 제안이다). 사유 증상과 그 증상에 걸리는 원료는 추천 근거·후보에서 뺀다
    care = norm["care_level"] != "none"
    care_label = CARE_LABEL.get(norm["care_level"])
    held = []
    if care:
        result["message"] = norm["care_message"]
        result["care_label"] = care_label
        # 사유 증상을 LLM 이 짚지 못했으면 어느 증상도 추천 근거로 쓰지 않는다(관심사 경로만 남는다)
        held_ids = set(norm["care_symptom_ids"]) & set(names) or {s["symptom_id"] for s in symptoms}
        held = [s for s in symptoms if s["symptom_id"] in held_ids]
        symptoms = [s for s in symptoms if s["symptom_id"] not in held_ids]
        result["step1"]["held_symptoms"] = [s["name"] for s in held]

    # 2. 그래프 후보
    symptom_ids = [s["symptom_id"] for s in symptoms]
    lifecycle = graph.run("lifestage_symptoms", age=patient["age"], sex=patient["sex"])
    lifecycle_ids = [r["symptom_id"] for r in lifecycle]
    by_symptom = symptom_candidates(graph, symptom_ids, lifecycle_ids, patient["sex"]) if symptom_ids else {}
    by_interest = interest_candidates(graph, patient["age"], patient["sex"])
    codes, drugs = safety_inputs(patient, graph.run("drug_matchers"))
    by_drug, drug_notes = drug_candidates(graph, drugs)
    if held:    # 진료 사유 증상에 걸리는 원료는 다른 증상이나 관심사로 걸려도 뺀다 (예: 저혈당 의심인데 혈당 관리 원료)
        held_hit = set(symptom_candidates(graph, [s["symptom_id"] for s in held], [], patient["sex"]))
        by_symptom = {k: v for k, v in by_symptom.items() if k not in held_hit}
        by_interest = {k: v for k, v in by_interest.items() if k not in held_hit}
        by_drug = {k: v for k, v in by_drug.items() if k not in held_hit}
        result["step1"]["held_ingredients"] = len(held_hit)
    result["step2"] = {
        "life_stage": lifecycle[0]["life_stage"] if lifecycle else None,
        "lifecycle_symptoms_matched": [names[s] for s in symptom_ids if s in lifecycle_ids],
        # 우선 증상이 어떤 근거로 생애주기 질환에 이어졌나: 원본 '증상 정규화' 분류(원본) 또는 질환 → 증상 추가 조사(추가 조사)
        "lifecycle_basis": [{"symptom": r["name"], "diseases": r["diseases"],
                             "origin": "·".join(o for o, b in (("원본", "category"), ("추가 조사", "disease")) if b in r["basis"]),
                             "refs": r["refs"]}
                            for r in lifecycle if r["symptom_id"] in symptom_ids],
        "symptom_candidates": len(by_symptom), "interest_candidates": len(by_interest),
        "drug_candidates": [f"{d['drug']} → {c['name']}" for c in by_drug.values() for d in c["depletions"]],
        "interests": sorted({i["interest"] for c in by_interest.values() for i in c["interests"]})}

    # 3. 조합
    merged = combine(by_symptom, by_interest, by_drug, drug_notes)
    result["step3"] = {f"score{n}": sum(1 for c in merged if c["score"] == n) for n in (4, 3, 2, 1, 0)}
    # 단계별 후보 목록(화면 하단 깔때기). 원료마다 어느 기준(증상/복용약/관심사)으로 올라왔는지 함께 남긴다
    by_name = lambda d: sorted(({"ingredient_id": k, "name": v["name"]} for k, v in d.items()), key=lambda x: x["name"])
    funnel = {"held_removed": result["step1"].get("held_ingredients", 0),
              "symptom": by_name(by_symptom), "interest": by_name(by_interest), "drug": by_name(by_drug),
              "merged": [_brief(c) for c in merged]}
    result["funnel"] = funnel

    # 4. 검증
    kept, dropped = verify(graph, merged, codes, drugs)
    funnel["kept"] = [_brief(c) for c in kept]
    funnel["dropped"] = dropped
    kept_ids = [c["ingredient_id"] for c in kept]
    synergy = {}
    for r in graph.run("synergy_partners", ingredient_ids=kept_ids):
        if is_public(r["source"]) and r["partner_id"] in kept_ids:
            synergy.setdefault(r["ingredient_id"], set()).add(r["partner_id"])
    basics = {r["ingredient_id"]: r for r in graph.run("ingredient_basics", ingredient_ids=kept_ids)}
    ranked = top_n(kept, n=len(kept), synergy=synergy, basics=basics)   # 근거 묶음 단위 전체 순서
    enrich(graph, ranked, set(kept_ids))
    slot_picks, missing = symptom_slots(ranked, symptom_ids)
    top = ranked[:TOP_N] + [c for c in slot_picks if c not in ranked[:TOP_N]]   # 증상 자리 원료는 TOP-10 밖이어도 싣는다
    funnel["groups"] = [{**_brief(c), "n_same": len(c.get("alternatives", []))} for c in ranked]
    funnel["top"] = [c["ingredient_id"] for c in top]
    result["step4"] = {"condition_codes": codes, "drug_classes": drugs, "kept": len(kept),
                       "excluded": len(dropped), "excluded_in_top_tiers": [d for d in dropped if d["routes"] != ["관심사"]][:10]}
    result["top10"] = [_export(c, n) for n, c in enumerate(top, 1)]
    if not top:
        result["outcome"] = care_label if care else "추천 없음"
        result.setdefault("message", "안전 검증과 출처 기준을 통과한 후보가 없습니다.")
        return result

    # 5. 선정
    #    5-1 증상 자리: 사용자 증상마다 그 증상을 직접 덮는 원료 중 순서가 가장 앞선 것을 먼저 고른다(symptom_slots)
    #    5-2 남은 자리: 증상 자리 원료를 뺀 TOP-10 에서 순서가 남은 자리 수번째보다 앞서면 확정(split_fixed),
    #        그 순서와 같은 동점 후보만 LLM 이 고른다. 동점 후보 수가 남은 자리 수와 같으면 고르지 않는다
    #    길항이거나 같은 원료군인 원료는 함께 고르지 않는다. LLM 이 규칙을 어기면 버리고 순위대로 채운다
    want = min(PICKS, len(top))
    by_id = {c["ingredient_id"]: c for c in top}
    clash = conflict
    auto = list(slot_picks)
    rest = [c for c in top if c not in auto]
    rule_why = {c["ingredient_id"]: conflict_why(c, o) for c in rest for o in auto if conflict(c, o)}
    rest = [c for c in rest if c["ingredient_id"] not in rule_why]
    slots = want - len(auto)
    fixed, pool, demoted = [], [], []
    if slots > 0 and rest:
        fixed, pool, why2 = split_fixed(rest, min(slots, len(rest)))
        rule_why.update(why2)
        demoted = [c for c in pool if c["ingredient_id"] in why2]
        slots -= len(fixed)
    n_cut = min(want - len(auto), len(rest))
    cut = sorted(rest, key=lambda c: c.get("group_key") or rank_key(c))[n_cut - 1] if n_cut > 0 else (auto[-1] if auto else top[0])
    rest_auto = []
    if len(pool) == slots:                          # 동점 후보가 남은 자리 수와 같으면 고를 것이 없다
        rest_auto, pool, slots = pool, [], 0
    must = auto + fixed + rest_auto
    choice = llm.select(view, [llm_view(c, n) for n, c in enumerate(top, 1)], [s["name"] for s in held],
                        must_include=[c["ingredient_id"] for c in must], choose_from=[c["ingredient_id"] for c in pool],
                        choose_n=slots)
    reasons = {p["ingredient_id"]: p["reason"] for p in choice["picks"]}
    pool_ids = {c["ingredient_id"] for c in pool}
    chosen = []
    for p in choice["picks"]:
        c = by_id.get(p["ingredient_id"])
        if c is None or c["ingredient_id"] not in pool_ids or c in chosen or len(chosen) >= slots:
            continue
        if any(clash(c, o) for o in must + chosen):
            rule_why[c["ingredient_id"]] = "LLM이 골랐지만 다른 선정 원료와 길항이라 버렸습니다"
            continue
        chosen.append(c)
    result["llm_pick_valid"] = len(chosen) == slots and {c["ingredient_id"] for c in must} <= set(reasons)
    for c in pool:                                  # 모자라면 순위대로 채운다(길항 쌍은 건너뛴다)
        if len(chosen) >= slots:
            break
        if c not in chosen and not any(clash(c, o) for o in must + chosen):
            chosen.append(c)
    picked_by = {**{c["ingredient_id"]: "순위" for c in fixed}, **{c["ingredient_id"]: "증상 자리" for c in auto},
                 **{c["ingredient_id"]: "자동" for c in rest_auto}, **{c["ingredient_id"]: "LLM" for c in chosen}}
    picks = [{"ingredient_id": c["ingredient_id"], "reason": reasons.get(c["ingredient_id"], "(LLM 사유 누락)")}
             for c in must + chosen]
    picks.sort(key=lambda p: top.index(by_id[p["ingredient_id"]]))
    result["selection"] = {"cut_score": cut["score"], "cut_n_direct": cut["n_direct"], "fixed": [c["name"] for c in fixed], "auto": [c["name"] for c in auto],
                           "rest_auto": [c["name"] for c in rest_auto],
                           "demoted": [c["name"] for c in demoted],
                           "uncovered_before": [names.get(s, s) for s in missing],
                           "llm_slots": slots, "llm_pool": [c["name"] for c in pool]}
    picked_ids = {p["ingredient_id"] for p in picks}
    funnel["picks"] = [p["ingredient_id"] for p in picks]
    result["outcome"] = f"{care_label} + 추천" if care else "추천"
    result["summary"] = choice["summary"]
    # 고른 후보보다 순위가 앞서는데 고르지 않은 후보와, LLM 이 같은 응답에서 적은 이유. 이유를 안 적은 후보는 why 를 None 으로 둔다
    last = max(top.index(by_id[i]) for i in picked_ids)
    said = {s["ingredient_id"]: s["why"] for s in choice.get("skipped", []) if s["ingredient_id"] in by_id}
    result["skipped"] = [{"ingredient_id": c["ingredient_id"], "name": c["name"], "rank": n, "score": c["score"],
                          "why": rule_why.get(c["ingredient_id"]) or said.get(c["ingredient_id"]),
                          "why_by": "규칙" if c["ingredient_id"] in rule_why else "LLM"}
                         for n, c in enumerate(top[:last], 1) if c["ingredient_id"] not in picked_ids]
    result["picks"] = []
    for p in picks:
        c = by_id[p["ingredient_id"]]
        result["picks"].append({
            **_export(c, top.index(c) + 1), "reason": p["reason"],
            "take_together": [{"name": s["partner"], "effect": s["effect"], "source": s["source"], "url": s["url"],
                               "already_picked": s["partner_id"] in picked_ids}
                              for s in c["synergy"] if s["passed_safety"]][:2],
            "picked_by": picked_by[c["ingredient_id"]],
            "conflicts_with_picks": [a for a in c["antagonists"] if {a["a_id"], a["b_id"]} <= picked_ids]})
    return result


_DOSE_UNIT = r"(?:mg|g|μg|ug|mcg|IU|CFU|mL|ml|kcal)"
_TAG_STOP = {"건강", "관리", "개선", "기능", "활동", "유지", "완화", "강화", "도움"}


def _claim_words(labels):
    """기능성 태그·관심사 이름 → 원문 줄에서 찾을 낱말. 예: '혈행·혈관 건강' → 혈행, 혈관 / '기억력·인지 기능' → 기억력, 인지."""
    words = []
    for lb in labels:
        for w in re.split(r"[·&/\s(),]+", lb or ""):
            w = re.sub(r"(건강|관리|개선|기능|활동)$", "", w)
            if w and w not in _TAG_STOP and w not in words:
                words.append(w)
    return words


def _eok(m):
    n = int(m.group(0).replace(",", ""))
    if n >= 10**12 and n % 10**12 == 0:
        return f"{n // 10**12:,}조"
    return f"{n // 10**8:,}억" if n >= 10**8 and n % 10**8 == 0 else m.group(0)


def dose_label(intake, name, labels=()):
    """카드 제목에 붙일 1일 섭취량 범위. 원문 범위를 그대로 줄여 쓰고 최대치 하나로 바꾸지 않는다.

    - 기능성마다 용량이 다르면(줄 여러 개) 추천 근거의 기능성(labels) 낱말이 든 줄을 쓴다. 맞는 줄이 없으면 None
    - '…으로서' 기준 성분이 원료명과 다르면 기준 성분을 밝힌다(예: 바나바잎 추출물 → '코로솔산 0.45~1.3 mg')
    - 괄호 속 다른 단위·다른 제형 값(IU, 액상원료 등)은 뗀다. '이상'은 그대로 둔다
    - '…의 경우'처럼 원료 형태에 따라 갈리는 원문, 숫자가 없는 원문은 None (상세의 일일섭취량 원문을 본다)
    """
    if not intake or not re.search(r"\d", intake):
        return None
    t = (intake.replace("∼", "~").replace("～", "~").replace("（", "(").replace("）", ")")
         .replace("㎎", "mg").replace("㎍", "μg"))
    t = re.sub(r"\(\s*(" + _DOSE_UNIT + r")\s*(?:/\s*일?)?\s*\)", r"\1", t)    # '500 (mg/일)' → '500 mg'
    lines = [x.strip() for x in re.split(r"\n|\(\s*[가-하]\s*\)", t) if x.strip()]
    lines = [x for x in lines if re.search(r"\d", x) and re.search(r"(?<![A-Za-z])" + _DOSE_UNIT + r"(?![A-Za-z])", x)]
    if not lines:
        return None
    if len(lines) > 1:
        words = _claim_words(labels)
        hit = next((x for w in words for x in lines if w in x.split(":")[0]), None)
        if hit is None:
            return None
        lines = [hit]
    s = lines[0]
    if re.search(r"경우\s*:", s) and len(intake.splitlines()) > 1:
        return None
    s = s.rsplit(":", 1)[-1] if ":" in s else s                                  # 기능성 설명 머리 떼기
    s = re.sub(r"\([^()]*\d[^()]*\)", "", s)                                     # 괄호 속 다른 단위·제형 값
    s = re.sub(r"\s*/\s*일", "", s).strip().rstrip(".").strip()
    if s.startswith(name):                                                       # '보스웰리아추출물(FJH-BS) 800 mg'
        s = re.sub(r"^(?:으로서|로서|으로)\s*:?\s*", "", s[len(name):].strip())
    m = re.match(r"(.+?)\s*(?:으로서|로서|으로(?=\s*[\d:]))\s*:?\s*(.+)", s)
    if m:
        base, s = re.sub(r"으$", "", m.group(1).strip()), m.group(2).strip()
        core, b = re.sub(r"\s|\(.*?\)", "", name), re.sub(r"\s|\(.*?\)", "", base)
        if (base and len(base) <= 20 and b not in core and core not in b and not core.startswith(b[:3])
                and b not in ("신청원료", "원료")):
            s = re.sub(r"^총\s*", "총 ", base) + " " + s
    s = re.sub(r"\s*~\s*", "~", s)
    s = re.sub(r"(\d)\s*(" + _DOSE_UNIT + r")~([\d.,]+)\s*\2\b", r"\1~\3 \2", s)        # '10 mg~20 mg' → '10~20 mg'
    s = re.sub(r"\d{1,3}(?:,\d{3}){2,}", _eok, s)                                 # CFU 큰 수 → 억
    s = re.sub(r"\s+", " ", s).replace(" ml", " mL")                              # 표준 표기: mL
    return s if re.search(r"\d", s) and len(s) <= 40 else None


def card(c):
    """1차 화면 카드 한 장: 제목(원료명 + 섭취량), 한 줄 근거, 근거 종류. 전부 그래프 값으로 만들고 LLM 이 쓰지 않는다.

    한 줄 근거는 '사용자 증상 → 기전' 이다. 기전이 여럿이면 사용자 증상을 가장 많이 설명하는 기전을 쓴다.
    증상 근거가 없고 복용약 근거가 있으면 '약 복용 → 허가사항이 말하는 것' 을 쓴다.
    """
    intake = (c.get("detail") or {}).get("daily_intake")
    mech = None
    by_symptom = c["paths"] and ("증상" in c["routes"] or not (c["depletions"] or c["interests"]))
    if by_symptom:      # 간접 경로뿐이면 다른 기준 근거를 앞세운다
        by_mech = {}
        for p in c["paths"]:
            m = by_mech.setdefault(p["mechanism"], {"symptoms": [], "confirmed": False, "via": p["via"]})
            if p["symptom"] not in m["symptoms"]:
                m["symptoms"].append(p["symptom"])
            m["confirmed"] = m["confirmed"] or p["confirmed"]
        mech, m = max(by_mech.items(), key=lambda kv: (len(kv[1]["symptoms"]), kv[1]["confirmed"], kv[1]["via"] == "deficiency"))
        headline, tag = f"{'·'.join(m['symptoms'])} → {mech}", "드럭머거" if m["via"] == "deficiency" else "증상"
    elif c["depletions"]:
        d = c["depletions"][0]
        headline, tag = f"{d['drug']} 복용 → {d['effect']}", "복용약"
    else:
        names = sorted({i["interest"] for i in c["interests"]})
        headline, tag = f"{c.get('segment') or '또래'} 관심사 → {'·'.join(names)}", "관심사"
    # 섭취량은 카드 근거와 같은 기능성 줄을 쓴다: 증상 근거면 headline 기전의 기능성 → 다른 경로 기능성, 아니면 관심사
    interest_labels = [i["interest"] for i in c["interests"]]
    labels = ([p["function"] for p in c["paths"] if by_symptom and p["mechanism"] == mech and p["function"]]
              + (interest_labels if tag == "관심사" else [])
              + [p["function"] for p in c["paths"] if p["function"]] + interest_labels)
    dose = dose_label(intake, c["name"], labels)
    # 증상 근거로 올라온 카드에도 복용약 근거가 겹치면 한 줄 더 보여준다. B 등급은 참고라고 밝힌다
    drug = [f"{d['drug']} 복용 → {d['effect']}" for d in c["depletions"]] if by_symptom else []
    drug += [f"참고: {d['drug']} 복용 → {d['effect']}" for d in c["drug_notes"]]
    # 주의가 걸린 원료도 우선순위가 높으면 그대로 권하고, 주의 문구(그래프 원문)를 카드에 함께 싣는다
    return {"title": c["name"], "dose": dose, "headline": headline, "tag": tag, "drug": drug,
            "cautions": list(c.get("cautions", []))}


def _export(c, rank):
    """결과 파일에 남기는 후보 한 건. 구간별 출처를 그대로 적는다."""
    refs = sorted({s for p in c["paths"] for s in p["sources"]} | {d["source"] for d in c["depletions"]})
    if not refs:
        refs = ["식약처(건강기능식품 기능성 원료 고시·개별인정) 기능성 문구"]
    return {"rank": rank, "score": c["score"], "routes": c["routes"], "n_direct": c["n_direct"], "n_priority": c["n_priority"],
            "alternatives": c.get("alternatives", []), "synergy_with": c.get("synergy_with"),
            "same_evidence_as": c.get("same_evidence_as"),
            "ingredient_id": c["ingredient_id"], "name": c["name"],
            "kind": KIND_LABEL.get(c["kind"], c["kind"]), "functions": c.get("functions", []),
            "intake": c.get("intake"),
            "symptoms_covered": sorted({p["symptom"] for p in c["paths"]}),
            "lifecycle_symptoms": sorted({p["symptom"] for p in c["paths"] if p["lifecycle"]}),
            "paths": sorted({f"{p['symptom']} → {p['mechanism']} → " + (p["function"] or "드럭머거") for p in c["paths"]}),
            "drug_basis": [{k: d.get(k) for k in ("drug", "grade", "effect", "quote", "product", "url", "note")}
                           for d in c["depletions"] + c["drug_notes"]],
            "interests": sorted({i["interest"] for i in c["interests"]}),
            "interest_source": sorted({i["source"] for i in c["interests"]}),
            "references": refs,
            "reference_urls": (sorted({u for p in c["paths"] for u in p["urls"]})[:6]
                               + sorted({d["url"] for d in c["depletions"]})),
            "cautions": c.get("cautions", []),
            "detail": c.get("detail"), "card": card(c)}
