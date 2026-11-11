"""추천 파이프라인.

    1. LLM      주호소 정규화, 응급·진료 필요 판정 (필요하면 추천하지 않고 진료 권고)
    2. GraphDB  2-1 증상 후보 (사용자 증상 + 생애주기 증상 참고)   2-2 관심사 후보 (나이·성별)
                2-3 복용약 후보 (식약처 허가사항에 결핍·보충 문장이 있는 약 → 영양소)
    3. 조합     같은 원료는 하나로 합치고 갈래마다 1점: 증상 1점, 복용약 1점(A 등급만), 관심사 1점
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
]
# 기전 → 기능성 연결 중 주제가 어긋나는 것. 이 연결로만 닿는 증상은 '직접 경로로 걸린 증상'으로 세지 않는다.
# 동점을 가르는 데만 쓴다. 사람 검토 전 초안이다
INDIRECT_LINKS = {
    ("알코올 대사산물(아세트알데히드) 축적", "항산화·노화 관리"), ("지질대사 이상", "혈행·혈관 건강"),
    ("체지방 축적", "중성지질 관리"), ("HPA축 과활성", "피로·활력 개선"),
    ("수면-각성 리듬 불균형", "기억력·인지 기능"), ("신경전달 저하", "수면 건강"),
    ("심리적 스트레스", "수면 건강"), ("심리적 스트레스", "피로·활력 개선"),
    ("만성 저도 염증", "관절·연골 건강"), ("면역 방어력 저하", "장 건강·배변 활동"),
    ("산화 스트레스", "간 건강"), ("산화 스트레스", "피부 건강"), ("산화 스트레스", "혈행·혈관 건강"),
    ("장내 균총 불균형", "면역 건강"), ("말초 혈관 과수축", "혈압 관리"), ("근육량 감소", "피로·활력 개선"),
    ("피부 장벽·콜라겐 저하", "면역 건강"), ("식욕·포만 호르몬 변화", "수면 건강"),
} | {("여성호르몬 변화", t) for t in ("관절·연골 건강", "기억력·인지 기능", "눈 건강", "뼈·칼슘 건강", "수면 건강",
                                 "스트레스·긴장 완화", "피부 건강")}
ROUTES = ("증상", "복용약", "관심사")          # 동점일 때 앞선 갈래가 있는 후보가 먼저다
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
    B 등급(결핍이 이상반응으로만 적힘)은 다른 갈래로 올라온 후보에 붙이는 참고 표시로 따로 돌려준다."""
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
    """3. 같은 원료를 하나로 합치고 갈래마다 1점을 준다(증상, 복용약, 관심사). 점수가 같으면
    증상 > 복용약 > 관심사 갈래가 있는 쪽, 그다음 생애주기 우선 증상을 직접 경로로 덮는 수,
    그다음 직접 경로로 걸린 증상 수, 그다음 근거가 많은 순.

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
        c["routes"] = [name for name, hit in zip(ROUTES, (s, d, i)) if hit]
        c["score"] = len(c["routes"])
        direct = [p for p in c["paths"] if (p["mechanism"], p["function"]) not in INDIRECT_LINKS]
        c["n_direct"] = len({p["symptom_id"] for p in direct})
        c["n_priority"] = len({p["symptom_id"] for p in direct if p["lifecycle"]})
        merged.append(c)

    def order(c):
        return (-c["score"], [name not in c["routes"] for name in ROUTES], -c["n_priority"], -c["n_direct"],
                -c.get("n_symptoms", 0), -c.get("n_lifecycle", 0), -c.get("n_confirmed", 0),
                c.get("interest_order", (99, 99)), KIND_ORDER.get(c["kind"], 9), c["ingredient_id"])

    return sorted(merged, key=order)


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


def top_n(candidates, n=TOP_N, per_route=2):
    """점수순으로 n개. 같은 이름의 원료(업체별 개별인정 등)는 앞선 하나만 남기고,
    근거 경로가 똑같은 후보는 per_route 개까지만 담아 TOP-10 이 한 기능성으로 채워지지 않게 한다."""
    names, routes, out = set(), {}, []
    for c in candidates:
        name = re.sub(r"\s+", "", c["name"])
        route = (tuple(c["routes"]), frozenset((p["symptom_id"], p["mechanism"], p["function"]) for p in c["paths"]),
                 frozenset(i["interest"] for i in c["interests"]) if c["routes"] == ["관심사"] else None,
                 frozenset(d["drug_id"] for d in c["depletions"]) if not c["paths"] else None)
        if name in names or routes.get(route, 0) >= per_route:
            continue
        names.add(name)
        routes[route] = routes.get(route, 0) + 1
        out.append(c)
        if len(out) == n:
            break
    return out


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
    return {"rank": rank, "score": c["score"], "routes": c["routes"],
            "ingredient_id": c["ingredient_id"], "name": c["name"],
            "kind": KIND_LABEL.get(c["kind"], c["kind"]), "functions": c.get("functions", []),
            "symptom_paths": reasons[:8],
            "priority_symptoms": sorted({p["symptom"] for p in c["paths"] if p["lifecycle"]
                                         and (p["mechanism"], p["function"]) not in INDIRECT_LINKS}),
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
    # emergency 는 추천 없이 끝낸다. visit 는 진료 권고를 먼저 내고, 진료 사유가 된 증상을 뺀 나머지로 추천을 이어간다
    care = norm["care_level"] != "none"
    held = []
    if care:
        result["message"] = norm["care_message"]
        if norm["care_level"] == "emergency":
            result["outcome"] = "진료 권고"
            return result
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
        "symptom_candidates": len(by_symptom), "interest_candidates": len(by_interest),
        "drug_candidates": [f"{d['drug']} → {c['name']}" for c in by_drug.values() for d in c["depletions"]],
        "interests": sorted({i["interest"] for c in by_interest.values() for i in c["interests"]})}

    # 3. 조합
    merged = combine(by_symptom, by_interest, by_drug, drug_notes)
    result["step3"] = {f"score{n}": sum(1 for c in merged if c["score"] == n) for n in (3, 2, 1)}

    # 4. 검증
    kept, dropped = verify(graph, merged, codes, drugs)
    top = enrich(graph, top_n(kept), {c["ingredient_id"] for c in kept})
    result["step4"] = {"condition_codes": codes, "drug_classes": drugs, "kept": len(kept),
                       "excluded": len(dropped), "excluded_in_top_tiers": [d for d in dropped if d["routes"] != ["관심사"]][:10]}
    result["top10"] = [_export(c, n) for n, c in enumerate(top, 1)]
    if not top:
        result["outcome"] = "진료 권고" if care else "추천 없음"
        result.setdefault("message", "안전 검증과 출처 기준을 통과한 후보가 없습니다.")
        return result

    # 5. LLM 선정. 후보 밖 성분이나 길항 쌍을 고르면 순위대로 고른 것으로 되돌린다
    choice = llm.select(view, [llm_view(c, n) for n, c in enumerate(top, 1)], [s["name"] for s in held])
    by_id = {c["ingredient_id"]: c for c in top}
    picks, seen = [], set()
    for p in choice["picks"]:
        if p["ingredient_id"] in by_id and p["ingredient_id"] not in seen:
            seen.add(p["ingredient_id"])
            picks.append(p)
    want = min(PICKS, len(top))
    result["llm_pick_valid"] = len(picks) == want == len(choice["picks"])
    for c in top:                                   # 모자라면 순위대로 채운다
        if len(picks) >= want:
            break
        if c["ingredient_id"] not in seen:
            seen.add(c["ingredient_id"])
            picks.append({"ingredient_id": c["ingredient_id"], "reason": "(LLM 선정 누락, 우선순위로 보충)"})
    picks = picks[:want]
    picked_ids = {p["ingredient_id"] for p in picks}
    result["outcome"] = "진료 권고 + 추천" if care else "추천"
    result["summary"] = choice["summary"]
    result["picks"] = []
    for p in picks:
        c = by_id[p["ingredient_id"]]
        result["picks"].append({
            **_export(c, top.index(c) + 1), "reason": p["reason"],
            "take_together": [{"name": s["partner"], "effect": s["effect"], "source": s["source"], "url": s["url"],
                               "already_picked": s["partner_id"] in picked_ids}
                              for s in c["synergy"] if s["passed_safety"]][:2],
            "conflicts_with_picks": [a for a in c["antagonists"] if {a["a_id"], a["b_id"]} <= picked_ids]})
    return result


def card(c):
    """1차 화면 카드 한 장: 제목(원료명 + 섭취량), 한 줄 근거, 근거 종류. 전부 그래프 값으로 만들고 LLM 이 쓰지 않는다.

    한 줄 근거는 '사용자 증상 → 기전' 이다. 기전이 여럿이면 사용자 증상을 가장 많이 설명하는 기전을 쓴다.
    증상 근거가 없고 복용약 근거가 있으면 '약 복용 → 허가사항이 말하는 것' 을 쓴다.
    """
    intake = (c.get("detail") or {}).get("daily_intake")
    dose = intake.strip().rstrip(".") if intake and "\n" not in intake and len(intake) <= 28 else None
    if c["paths"]:
        by_mech = {}
        for p in c["paths"]:
            m = by_mech.setdefault(p["mechanism"], {"symptoms": [], "confirmed": False, "via": p["via"]})
            if p["symptom"] not in m["symptoms"]:
                m["symptoms"].append(p["symptom"])
            m["confirmed"] = m["confirmed"] or p["confirmed"]
        mech, m = max(by_mech.items(), key=lambda kv: (len(kv[1]["symptoms"]), kv[1]["confirmed"], kv[1]["via"] == "deficiency"))
        headline, tag = f"{'·'.join(m['symptoms'])} → {mech}", "결핍 보충" if m["via"] == "deficiency" else "증상"
    elif c["depletions"]:
        d = c["depletions"][0]
        headline, tag = f"{d['drug']} 복용 → {d['effect']}", "복용약"
    else:
        names = sorted({i["interest"] for i in c["interests"]})
        headline, tag = f"{c.get('segment') or '또래'} 관심사 → {'·'.join(names)}", "관심사"
    # 증상 근거로 올라온 카드에도 복용약 근거가 겹치면 한 줄 더 보여준다. B 등급은 참고라고 밝힌다
    drug = [f"{d['drug']} 복용 → {d['effect']}" for d in c["depletions"]] if c["paths"] else []
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
            "ingredient_id": c["ingredient_id"], "name": c["name"],
            "kind": KIND_LABEL.get(c["kind"], c["kind"]), "functions": c.get("functions", []),
            "intake": c.get("intake"),
            "symptoms_covered": sorted({p["symptom"] for p in c["paths"]}),
            "lifecycle_symptoms": sorted({p["symptom"] for p in c["paths"] if p["lifecycle"]}),
            "paths": sorted({f"{p['symptom']} → {p['mechanism']} → " + (p["function"] or "결핍 보충") for p in c["paths"]}),
            "drug_basis": [{k: d.get(k) for k in ("drug", "grade", "effect", "quote", "product", "url", "note")}
                           for d in c["depletions"] + c["drug_notes"]],
            "interests": sorted({i["interest"] for i in c["interests"]}),
            "interest_source": sorted({i["source"] for i in c["interests"]}),
            "references": refs,
            "reference_urls": (sorted({u for p in c["paths"] for u in p["urls"]})[:6]
                               + sorted({d["url"] for d in c["depletions"]})),
            "cautions": c.get("cautions", []),
            "detail": c.get("detail"), "card": card(c)}
