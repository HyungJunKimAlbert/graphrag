"""LLM 호출 두 군데: 1단계(증상 정규화 · 진료 필요 판정)와 5단계(TOP-10 중 3개 선정).

OpenAI Responses API 의 구조화 출력을 쓴다. 같은 요청은 out/llm_cache 의 응답을 다시 써서 한 번만 과금된다.
키는 wellcheck/.env 의 OPENAI_API_KEY 에서 읽는다.
"""
import hashlib
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT.parent / ".env"
CACHE = ROOT / "out" / "llm_cache"
MODEL = "gpt-6-luna"
EFFORT = "low"
NORMALIZE_EFFORT = "low"      # 2026-11-11 비교: low 는 medium 보다 1.6초 빠르고 정규화 결과는 같았다. none 은 '약물 복용 후 메스꺼움' 구분을 놓쳤다.
                              # 진료 판정은 low·medium 모두 실행마다 흔들려(kang 5회 중 low 4회·medium 1회 누락) 약-증상 조합은 규칙(pipeline.apply_care_rules)으로 고정한다

NORMALIZE_SYSTEM = """당신은 건강기능식품 추천 서비스의 첫 단계를 맡는다. 사용자 정보를 읽고 두 가지를 판단한다.

입력 형태
- chief_complaint 는 약사가 상담 중에 적은 짧은 메모다. 완전한 문장이 아니라 "다리 근육통, 밤에 자주 깸", "식후 더부룩함·속쓰림, 1주째", "콧물·기침 3일째, 종합감기약 복용 중" 같은 짧은 구가 쉼표, 가운뎃점(·), 슬래시로 이어진다. 문장으로 들어와도 같은 방식으로 처리한다.

1. 주호소 정규화
   - 먼저 구분 기호로 메모를 구 단위로 나눈다. "손발 저림·차가움"처럼 앞말을 함께 쓰는 구는 "손발 저림", "손발 차가움"으로 풀어서 본다.
   - 각 구를 셋 중 하나로 분류한다.
     · symptoms: 지금 겪는 증상이고 아래 증상 목록에 뜻이 같은 항목이 있다. phrase 에는 메모에 적힌 표현을 그대로 옮긴다.
     · unmatched: 증상이지만 목록에 뜻이 같은 항목이 없다. 원문 그대로 적는다.
     · context: 증상이 아닌 정보. 기간("1주째", "3일째"), 복용 중인 약("종합감기약 복용 중"), 계기("당뇨약 증량 후"), 날짜. 구 안에 증상과 함께 붙어 있으면 그 부분만 떼어 적는다.
   - 억지로 맞추지 않는다. 부위나 글자가 비슷하다는 이유로 고르지 않는다. 근육통은 관절 통증도 근육경련(쥐남)도 아니고, 가슴 답답함은 소화불량이 아니다. 이런 표현은 unmatched 다.
   - 한 구가 목록의 여러 항목에 걸치면 뜻이 가장 좁고 정확한 항목 하나를 고른다. 항목 옆 괄호의 구분 기준을 따른다. 구에 서로 다른 증상이 실제로 둘 들어 있을 때만 둘을 고른다.
   - 주호소에 없는 증상을 질환명, 검진 수치, 복용약에서 추측해 넣지 않는다. 같은 증상을 두 번 넣지 않는다.

2. 응급·진료 필요 판정
   - 판정의 중심은 주호소 증상이다. 나머지 사용자 정보(질환, 검진 이상, 복용약)는 아래 두 가지를 확인하는 데만 쓴다.
     (가) medications 에 신규·증량·변경 표시가 있는 약이 있고, 주호소 증상이 그 약의 알려진 부작용과 맞는가. 맞거나, 소화기 증상·어지러움·부종·근육 증상처럼 약 부작용으로 흔한 증상이라 관련을 배제하기 어려우면 메모에 약 이야기가 없어도 visit 다. 그 약과 이어지지 않는 증상(예: 진통제를 새로 먹는 사람의 뱃살 증가)이면 약 변경은 visit 근거가 아니다.
     (나) 주호소 증상이 앓고 있는 질환이나 복용약의 위험 신호인가(예: 당뇨약 복용 중 공복 현기증).
     (다) 약 변경 표시가 없어도 아래 조합은 visit 다. 오래 먹어 온 약이어도 이상반응 확인이 먼저다.
          · 항혈소판제·항응고제(아스피린, 클로피도그렐, 와파린 등) 복용 중 속쓰림, 위통, 소화불량 같은 위장 증상
          · 스타틴(아토르바스타틴, 로수바스타틴 등) 복용 중 근육경련(쥐남), 근육통, 근력 저하 같은 근육 증상
          이 조합에 해당하는 증상은 모두 care_symptom_ids 에 넣는다.
   - 나이가 많다, 만성질환이 있다, 검진 수치가 나쁘다, 약을 여러 개 먹는다는 사실은 그 자체로 visit 근거가 아니다. 주호소 증상과 직접 이어지지 않는 검진 이상이나 약을 근거로 삼지 않는다.
   - emergency: 지금 바로 응급실이나 119 가 필요한 신호. 예: 흉통, 가슴 답답함과 호흡곤란, 한쪽 마비나 말 어눌함, 의식 저하, 심한 출혈, 극심한 두통, 자해 생각.
   - visit: 응급은 아니지만 영양제보다 진료가 먼저인 경우. 예: 약을 바꾸거나 늘린 뒤 생긴 증상(약 부작용 의심), 복용 중인 약이나 앓는 질환이 원인일 수 있는 새 증상, 원인 모를 체중 감소·부종·출혈, 75세 이상에서 기억력 저하와 기분 저하가 함께 있는 경우처럼 질환 감별이 필요한 증상.
   - none: 생활 속 불편 수준이라 건강기능식품 추천을 진행해도 되는 경우. 만성질환이 있거나 약을 먹는다는 사실만으로는 visit 로 판정하지 않는다. 변비, 소화불량, 피로, 수면 불편처럼 흔한 증상이 며칠에서 몇 주 이어졌다는 기간만으로도 visit 로 판정하지 않는다. 혈변, 체중 감소, 발열, 심한 통증 같은 경고 신호가 함께 적혀 있을 때 visit 다.
   - 원인 모를 부종은 다른 정보가 없어도 visit 다.
   - care_symptom_ids 에는 emergency 나 visit 판정의 사유가 된 증상의 symptom_id 를 적는다(symptoms 에 고른 것 중에서). 진료와 무관한 나머지 증상은 넣지 않는다. none 이면 빈 배열이다.
   - care_reason 에는 판정 근거를 한 문장으로 적는다. emergency 나 visit 이면 care_message 에 사용자에게 보여줄 진료 권고 문구를 2문장 이내로 적고, none 이면 빈 문자열로 둔다.

증상 목록 (symptom_id 이름 (구분 기준)):
{symptoms}"""

# 뜻이 가까운 증상 항목끼리의 구분 기준. 평가 세트(test/normalize_cases.json)에서 헷갈린 것만 적는다
SYMPTOM_NOTES = {
    "S052": "약을 시작·변경·증량한 뒤 생긴 메스꺼움·울렁거림. 약과의 관련이 적혀 있으면 울렁거림 대신 이것",
    "S056": "약과 관련 없이 속이 메스껍거나 울렁거림",
    "S031": "다리·발·얼굴이 부음. 부위가 적힌 부기는 이것",
    "S034": "부위 없이 몸이 전체적으로 붓는 느낌",
    "S036": "자다가 깸, 새벽에 깸",
    "S032": "잠들기 어려움, 잠이 안 옴",
    "S043": "자도 개운하지 않음, 얕은 잠",
    "S012": "기운이 없음, 피곤함, 눕고 싶음",
    "S018": "쉬거나 자도 피로가 안 풀림",
    "S021": "피로가 오래 이어짐(수개월)",
    "S062": "체력이 예전보다 떨어짐",
    "S023": "의욕이 없음, 아무것도 하기 싫음",
    "S058": "음주 후 불편감. 술 마신 다음 날의 숙취, 몸 무거움",
    "S029": "더부룩함, 가스 참, 배가 빵빵함",
    "S040": "소화가 안 됨, 체함",
    "S041": "속쓰림, 신물 올라옴",
    "S006": "무릎·어깨 등 관절이 아프거나 시큼함. 근육통은 아님",
    "S007": "쥐가 남, 근육이 뭉치며 경련. 근육통은 아님",
    "S057": "관절이 뻣뻣함, 움직이기 불편함",
    "S002": "감기에 자주 걸림, 감기 기운, 콧물·기침 같은 감기 증상",
    "S014": "눈이 뻑뻑함, 건조함",
    "S066": "눈이 침침함",
    "S046": "글씨나 사물이 잘 안 보임",
    "S060": "소변을 자주 봄, 야간뇨",
    "S003": "얼굴 화끈거림·달아오름, 식은땀 같은 갱년기 증상",
}

NORMALIZE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["symptoms", "unmatched", "context", "care_level", "care_symptom_ids", "care_reason", "care_message"],
    "properties": {
        "symptoms": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["symptom_id", "phrase"],
            "properties": {"symptom_id": {"type": "string"}, "phrase": {"type": "string"}}}},
        "unmatched": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "array", "items": {"type": "string"}},
        "care_level": {"type": "string", "enum": ["none", "visit", "emergency"]},
        "care_symptom_ids": {"type": "array", "items": {"type": "string"}},
        "care_reason": {"type": "string"},
        "care_message": {"type": "string"}}}

SELECT_SYSTEM = """당신은 건강기능식품 추천 서비스의 마지막 단계를 맡는다. 서버가 그래프에서 뽑아 안전 검증까지 마친 후보 목록을 받아, 이 사용자에게 보여줄 카드를 완성한다. 순서가 앞서는 후보와 규칙으로 정해진 후보는 이미 확정됐고(must_include), 남은 자리(choose_n 개)만 choose_from 안에서 고른다.

규칙
- must_include 의 후보는 전부 picks 에 넣는다. 빼거나 바꾸지 않는다.
- 남은 자리는 choose_from 에 있는 ingredient_id 중에서만 choose_n 개 고른다. choose_from 의 후보는 서버 기준으로 순서를 가를 수 없는 동점 후보다. choose_n 이 0 이면 더 고르지 않는다.
- 후보 목록에 없는 성분을 추가하지 않는다.
- 후보는 우선순위 순으로 정렬되어 있다. 첫 기준은 n_symptoms_direct(직접 경로로 덮는 사용자 증상 수)이고 많을수록 무조건 앞이다. 기전 하나로 설명되는 증상들은 하나로 센다. 그 수가 같을 때 score 로 가른다. score 는 증상(직접 경로가 있을 때만), 복용약, 관심사 기준에 각 1점, priority_symptoms 가 있으면 1점을 더한 값이다. choose_from 안에서는 순위를 존중하되, 아래 사정이 있으면 뒤 순위를 고를 수 있다. 확정된 후보(must_include)와의 조합을 함께 본다.
  · priority_symptoms 가 있는 후보(이 나이·성별에 흔한 질환의 증상과 겹치는 사용자 증상을 직접 덮는 후보)를 먼저 검토한다
  · 사용자의 증상을 더 많이, 더 직접 설명하는 후보
  · 서로 시너지가 있는 조합(synergy 에 "(후보에 있음)" 이 붙은 짝은 함께 고르는 것을 먼저 검토한다), 서로 흡수를 방해하지 않는 조합(antagonists 는 함께 고르지 않는다)
  · 같은 기능의 성분만 3개 고르지 않고, 사용자의 증상을 고루 덮는 조합. 같은 원료군(예: 인삼과 홍삼)을 함께 고르지 않는다
- cautions(주의 문구)가 있다는 이유로 후보를 빼거나 뒤로 미루지 않는다. 금지 성분은 서버가 이미 뺐고, 주의 문구는 서버가 카드에 그대로 붙여 함께 보여준다. 우선순위가 높으면 주의가 걸려 있어도 고른다. reason 에서 주의 문구를 바꿔 쓰거나 안전하다고 쓰지 않는다.
- 후보가 3개보다 적으면 있는 만큼만 고른다.
- drug_basis 는 사용자가 먹는 약의 식약처 허가사항 문장이다. 복용약 갈래로 고를 때는 그 문장이 말하는 만큼만 쓴다(문장에 없는 결핍 증상이나 수치를 덧붙이지 않는다). drug_notes 는 참고 정보일 뿐이라 고르는 근거로 쓰지 않는다.
- reason 은 사용자에게 보여줄 설명이다. 후보에 적힌 근거(증상, 기전, 기능성, 복용약, 관심사)만 써서 2문장 이내로 쓴다. 섭취량 같은 수치는 후보의 intake 값을 그대로 옮기고, 값이 없으면 수치를 쓰지 않는다. 질병을 치료·예방한다고 쓰지 않는다.
- summary 에는 세 성분을 고른 이유를 한두 문장으로 쓴다.
- skipped: choose_from 안에서 고른 후보보다 순위(rank)가 앞서는데 고르지 않은 후보가 있으면, 그 후보마다 ingredient_id 와 고르지 않은 이유(why)를 한 문장으로 적는다. 실제로 판단에 쓴 이유를 후보에 적힌 정보로 구체적으로 쓴다(예: 어느 후보와 무엇이 겹치는지, 어느 후보가 무엇을 더 덮는지). 순위대로 위에서부터 골랐으면 빈 배열로 둔다.
- held_symptoms 가 있으면 그 증상은 진료가 먼저라고 이미 안내한 증상이다. 그 증상을 고르는 근거로 쓰지 않고, reason 과 summary 에서 그 증상에 도움이 된다고 쓰지 않는다."""

SELECT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks", "summary", "skipped"],
    "properties": {
        "picks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["ingredient_id", "reason"],
            "properties": {"ingredient_id": {"type": "string"}, "reason": {"type": "string"}}}},
        "summary": {"type": "string"},
        "skipped": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["ingredient_id", "why"],
            "properties": {"ingredient_id": {"type": "string"}, "why": {"type": "string"}}}}}}


def _key():
    for line in ENV.read_text().splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{ENV} 에 OPENAI_API_KEY 가 없다")


class LLM:
    def __init__(self, model=MODEL, effort=EFFORT, use_cache=True, normalize_effort=NORMALIZE_EFFORT):
        """effort 는 5단계 선정, normalize_effort 는 1단계 정규화의 reasoning effort.
        use_cache=False 면 캐시를 읽지도 쓰지도 않고 매번 호출한다(추론 시간·토큰 측정용)."""
        self.model, self.effort, self.use_cache = model, effort, use_cache
        self.normalize_effort = normalize_effort
        self._client = None
        self.calls = self.cached = 0
        self.log = []            # 실제 호출마다 {task, seconds, input_tokens, output_tokens, ...}

    def complete(self, task, system, user, schema, effort=None):
        effort = effort or self.effort
        raw = json.dumps([self.model, effort, task, system, user, schema], ensure_ascii=False, sort_keys=True)
        path = CACHE / task / (hashlib.sha256(raw.encode()).hexdigest()[:16] + ".json")
        if self.use_cache and path.exists():
            self.cached += 1
            return json.loads(path.read_text(encoding="utf-8"))
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=_key())
        t0 = time.perf_counter()
        res = self._client.responses.create(
            model=self.model, reasoning={"effort": effort},
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": task, "strict": True, "schema": schema}})
        seconds = time.perf_counter() - t0
        out = json.loads(res.output_text)
        u = res.usage
        self.log.append({"task": task, "effort": effort, "seconds": round(seconds, 3),
                         "input_tokens": u.input_tokens if u else None, "output_tokens": u.output_tokens if u else None,
                         "cached_tokens": (getattr(u.input_tokens_details, "cached_tokens", 0) or 0) if u else None,
                         "reasoning_tokens": (getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0) if u else None})
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        self.calls += 1
        return out

    def normalize(self, patient_view, symptoms):
        system = NORMALIZE_SYSTEM.format(symptoms="\n".join(
            f"{s['symptom_id']} {s['name']}" + (f" ({SYMPTOM_NOTES[s['symptom_id']]})" if s["symptom_id"] in SYMPTOM_NOTES else "")
            for s in symptoms))
        return self.complete("normalize", system, json.dumps(patient_view, ensure_ascii=False, indent=1),
                             NORMALIZE_SCHEMA, self.normalize_effort)

    def select(self, patient_view, candidates, held_symptoms=(), must_include=(), choose_from=(), choose_n=0):
        payload = {"user": patient_view, "must_include": list(must_include), "choose_from": list(choose_from),
                   "choose_n": choose_n, "candidates": candidates}
        if held_symptoms:        # 진료 권고와 함께 내는 추천: 진료가 먼저인 증상은 근거로 쓰지 않는다
            payload["held_symptoms"] = list(held_symptoms)
        user = json.dumps(payload, ensure_ascii=False, indent=1)
        return self.complete("select", SELECT_SYSTEM, user, SELECT_SCHEMA)
