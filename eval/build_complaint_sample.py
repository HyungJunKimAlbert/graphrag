"""샘플 형식 주호소 테스트셋의 명세(정답 먼저). python -m eval.build_complaint_sample

증상 조합·프로필은 이 코드가 정한다(시드 고정). 문장은 이 명세를 받은 생성 담당이 쓰고, 정규화에 답하는 쪽은 정답을 보지 않는다.
- 분류표 키워드 71개(분류, 키워드) 쌍마다 6회, 분류표 밖 증상 12개마다 5회
- 일반 270건(증상 1~3개) + 위험 신호 30건(절반은 일반 증상 1개 동반) = 300건
"""
import json
import random
from pathlib import Path

from rxrag import taxonomy

OUT = Path(__file__).resolve().parent / "complaint_sample_spec.json"
SEED = 20261109
PER_KEYWORD, PER_OUTSIDE = 6, 5
OUTSIDE = ["근육통", "야간뇨", "어지러움", "어깨통증", "기침", "코막힘", "손발톱 약화", "피부 건조함", "가려움",
           "잇몸 출혈", "구강통증", "무감각"]
RED_FLAGS = ["쥐어짜는 가슴 통증", "숨이 차서 말을 못 함", "한쪽 팔다리 힘 빠짐", "말이 어눌해짐", "얼굴 한쪽 처짐",
             "검은 변", "피 섞인 구토", "혈변", "갑자기 한쪽 눈 안 보임", "평생 처음 겪는 심한 두통", "의식 잃고 쓰러짐",
             "죽고 싶은 생각", "입술·혀 붓고 숨쉬기 힘듦", "고열과 목 뻣뻣함", "이유 없이 몇 달 새 체중 크게 줆",
             "한쪽 다리만 갑자기 붓고 아픔", "명치 통증과 식은땀", "코피가 안 멈춤(항응고제 복용)", "가족을 못 알아봄",
             "계단 오를 때 가슴 조임", "자다가 숨 막혀 깸", "소변이 콜라색", "머리 부딪힌 뒤 계속 토함",
             "혈당 400 넘고 계속 토함", "심한 복통으로 못 움직임", "왼팔 저리고 가슴 답답함", "갑자기 앞이 커튼 친 듯 안 보임",
             "토할 때 피", "소변에 피", "갑자기 숨이 가쁘고 입술이 파래짐"]
AGES = [(20, 5), (30, 10), (40, 15), (50, 20), (60, 20), (70, 20), (80, 10)]
FEMALE_YOUNG = {"생리통", "생리주기 불규칙"}


def profile(rng, items):
    names = {s["name"] for s in items}
    if names & FEMALE_YOUNG:
        return f"여성, {rng.choice([20, 30, 40])}대"
    if "갱년기 증상" in names:
        return f"{'여성' if rng.random() < 0.8 else '남성'}, {rng.choice([40, 50])}대"
    if rng.random() < 0.1:
        return ""  # 샘플-011·012처럼 프로필 없는 입력
    age = rng.choices([a for a, _ in AGES], weights=[w for _, w in AGES])[0]
    return f"{rng.choice(['남성', '여성'])}, {age}대"


def build(seed=SEED):
    rng = random.Random(seed)
    slots = [{"kind": "taxonomy", "category": c["category"], "name": k} for c in taxonomy.load() for k in c["keywords"]
             for _ in range(PER_KEYWORD)]
    slots += [{"kind": "outside", "category": "분류표 밖 증상", "name": n} for n in OUTSIDE for _ in range(PER_OUTSIDE)]
    rng.shuffle(slots)
    sizes = [1] * 114 + [2] * 111 + [3] * 45 + [1] * 15  # 마지막 15개는 위험 신호 동반 증상
    assert sum(sizes) == len(slots), (sum(sizes), len(slots))
    rng.shuffle(sizes[:270])
    groups, i = [], 0
    for n in sizes:
        groups.append(slots[i:i + n]); i += n
    # 한 케이스에 같은 이름(부종·소화불량은 두 분류에 있음)이 겹치면 다른 케이스와 맞바꾼다
    for _ in range(10000):
        bad = [g for g in groups if len({s["name"] for s in g}) < len(g)]
        if not bad:
            break
        g, h = bad[0], rng.choice(groups)
        a, b = rng.randrange(len(g)), rng.randrange(len(h))
        g[a], h[b] = h[b], g[a]
    assert all(len({s["name"] for s in g}) == len(g) for g in groups)
    cases = []
    for n, g in enumerate(groups[:270], 1):
        cases.append({"id": f"SF-{n:03d}", "group": "일반", "profile": profile(rng, g), "symptoms": g, "red_flag": None})
    flags = RED_FLAGS[:]
    rng.shuffle(flags)
    for n, flag in enumerate(flags, 271):
        extra = groups[270 + n - 271] if n - 271 < 15 else []
        cases.append({"id": f"SF-{n:03d}", "group": "위험", "profile": profile(rng, extra), "symptoms": extra,
                      "red_flag": flag})
    return cases


if __name__ == "__main__":
    cases = build()
    OUT.write_text(json.dumps({"seed": SEED, "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(cases), Counter(len(c["symptoms"]) for c in cases), Counter(s["name"] for c in cases for s in c["symptoms"]).most_common(3))
    print(OUT)


def assemble(spec, text, equivalents):
    """명세 + 생성 문장 → normalize_eval 테스트셋 형식. 분류표 밖 증상은 표준 이름 또는 원문 조각이 분류표 밖 텍스트에 있으면 맞힘."""
    by_id = {c["id"]: c for c in text["cases"]}
    cases = []
    for s in spec["cases"]:
        t = by_id[s["id"]]
        required = []
        for sym, ph in zip(s["symptoms"], t["phrases"]):
            assert sym["name"] == ph["name"] and ph["phrase"] in t["utterance"], s["id"]
            extra = [ph["phrase"]] if sym["kind"] == "outside" else []
            required.append([sym["name"], *extra, *ph["also_ok"]])
        cases.append({"id": s["id"], "group": s["group"], "level": "샘플형식", "utterance": t["utterance"],
                      "profile": s["profile"], "required": required, "forbid": [], "route": "",
                      "red_flag": s["red_flag"] is not None, "no_match": False, "expect": [], "allow": [],
                      "note": s["red_flag"] or "", "categories": [x["category"] for x in s["symptoms"]]})
    return {"name": "샘플 형식 주호소 테스트셋", "status": "Claude 생성(정답 먼저). 약사 검토 전. 결과를 본 뒤 정답을 고치지 않는다",
            "created": "2026-11-09", "equivalents": equivalents, "cases": cases}
