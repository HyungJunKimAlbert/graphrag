"""② 주호소 정규화. 자유 문장 → 14개 분류·증상 키워드·위험 신호. 결과는 엔진 build_context(normalized=...) 형식이다."""
from .legacy import SEX_KEYS
from .taxonomy import prompt_text

TASK = "normalize"
UNMATCHED_KINDS = ["분류표 밖 증상", "질환명", "약 관련", "기타"]

SYSTEM = """당신은 건강검진 상담 서비스에서 환자가 쓴 주호소 문장을 증상 분류표에 맞춰 정리합니다.

[증상 분류표]
{taxonomy}

[규칙]
- matches: 문장에 나온 증상을 위 분류 중 하나에 넣습니다. category는 분류표의 분류명을 그대로 씁니다.
- symptoms에는 그 분류의 키워드 중 문장과 뜻이 맞는 것을 키워드 표기 그대로 넣습니다. 분류는 분명하지만 맞는 키워드가 없으면 symptoms를 빈 리스트로 둡니다.
- 부정된 증상(없음·아님·않음, 예: "두통은 없고")은 어디에도 넣지 않습니다.
- 분류표 키워드에 없는 증상은 unmatched에 kind="분류표 밖 증상"으로 넣습니다. 질환명은 kind="질환명", 약 이름·복용 관련 내용은 kind="약 관련", 나머지는 kind="기타"입니다. text에는 문장의 표현을 짧게 옮깁니다.
- 흉통·호흡곤란·의식저하·마비·혈변/토혈·급격한 체중감소 등 응급 신호가 있으면 red_flag=true로 하고 red_flag_reason에 그 신호를 적습니다. 없으면 red_flag=false, red_flag_reason="".
- 문장에 없는 증상을 추측해 넣지 않습니다."""

USER = "환자: {profile}\n주호소: {complaint}"


def schema(cats):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["matches", "unmatched", "red_flag", "red_flag_reason"],
        "properties": {
            "matches": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["category", "symptoms"],
                "properties": {"category": {"type": "string", "enum": [c["category"] for c in cats]},
                               "symptoms": {"type": "array", "items": {"type": "string"}}}}},
            "unmatched": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["text", "kind"],
                "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": UNMATCHED_KINDS}}}},
            "red_flag": {"type": "boolean"},
            "red_flag_reason": {"type": "string"},
        },
    }


def profile(patient):
    sex = {"M": "남성", "F": "여성"}[SEX_KEYS.get(str(patient["sex"]).strip().lower(), patient["sex"])]
    return f"{sex}, {int(float(patient['age'])) // 10 * 10}대"


def prompt(complaint, profile_text, cats):
    return SYSTEM.format(taxonomy=prompt_text(cats)), USER.format(profile=profile_text, complaint=complaint.strip())


def flatten(parsed, cats):
    """엔진 llm.flatten과 같은 규칙. 분류표 밖 category는 버리고 unmatched(기타)로 남긴다."""
    known = {c["category"] for c in cats}
    symptoms, categories, unmatched = [], [], list(parsed.get("unmatched", []))
    for m in parsed.get("matches", []):
        if m["category"] not in known:
            unmatched.append({"text": f"{m['category']}: {', '.join(m['symptoms'])}".rstrip(": "), "kind": "기타"})
            continue
        symptoms += m["symptoms"]
        if not m["symptoms"]:
            categories.append(m["category"])
    return {"symptoms": sorted(set(symptoms)), "categories": sorted(set(categories)), "unmatched": unmatched,
            "red_flag": bool(parsed.get("red_flag")), "red_flag_reason": parsed.get("red_flag_reason", "")}


def run(patient, client, cats):
    complaint = (patient.get("complaint") or "").strip()
    if not complaint:
        return None
    system, user = prompt(complaint, profile(patient), cats)
    return flatten(client.complete_json(TASK, system, user, schema(cats)), cats)


def merge(patient, normalized):
    """patient["symptoms"] ∪ normalized["symptoms"]. 주호소가 없으면 None(엔진이 patient["symptoms"]를 쓴다)."""
    if normalized is None:
        return None
    given = list(patient.get("symptoms") or [])
    return {**normalized, "symptoms": given + [s for s in normalized["symptoms"] if s not in given]}
