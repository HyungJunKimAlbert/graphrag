"""가상 환자셋을 만들고 환자마다 must_not(금지 성분 ID)을 계산해 eval/patients.json에 쓴다. 한 번 만들고 커밋한 뒤 수정 금지.

must_not = 추천 가능한 전체 성분 중 이 환자에게 avoid 판정인 것 전부(CAUTION_POLICY keep 기준).
후보 풀은 주호소 정규화(LLM) 결과에 따라 달라지지만 금지 판정은 성분·환자 조건만으로 정해지므로, 풀이 아니라 전체 성분에 판정한다.
"""
import json
import sys
from pathlib import Path

from rxrag import contract, graph, legacy

OUT = Path(__file__).resolve().parent / "patients.json"
FIXTURE = legacy.GRAPHDB / "tests" / "fixtures" / "patient_80m.json"
Q_ALL = "MATCH (i:Ingredient) WHERE i.recommendable RETURN i.ingredient_id ORDER BY i.ingredient_id"


def d(*names):
    return [{"name": n} for n in names]


def base_patients():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = [("V-01", "픽스처", "엔진 80세 남성 픽스처(주호소+증상)", fixture, None)]
    archive = [  # archive/qa-graphrag eval/questions.json P-01~P-15(P-13 제외)
        ("P-01", {"age": 80, "sex": "M", "drugs": d("와파린", "메트포르민"), "complaint": "다리에 쥐가 자주 나요"}),
        ("P-02", {"age": 32, "sex": "F", "pregnant": True, "complaint": "피로가 심해요"}),
        ("P-03", {"age": 62, "sex": "M", "drugs": d("아토르바스타틴"), "complaint": "근육이 쑤시고 아파요"}),
        ("P-04", {"age": 30, "sex": "F", "pregnant": False, "lactating": True, "complaint": "변비가 있어요"}),
        ("P-05", {"age": 12, "sex": "M", "complaint": "눈이 피로해요"}),
        ("P-06", {"age": 16, "sex": "F", "complaint": "늘 피곤해요"}),
        ("P-07", {"age": 70, "sex": "F", "drugs": d("와파린"), "complaint": "손발이 차고 혈액순환이 안 되는 것 같아요"}),
        ("P-08", {"age": 58, "sex": "M", "drugs": d("메트포르민", "글리메피리드"), "complaint": "손발이 저려요"}),
        ("P-09", {"age": 45, "sex": "F", "pregnant": False, "conditions": ["갑상선 질환"], "drugs": d("레보티록신"),
                  "complaint": "머리카락이 많이 빠져요"}),
        ("P-10", {"age": 50, "sex": "F", "drugs": d("프레드니솔론"), "complaint": "뼈가 약해질까 걱정돼요"}),
        ("P-11", {"age": 66, "sex": "M", "drugs": d("로사르탄"), "complaint": "잠을 잘 못 자요"}),
        ("P-12", {"age": 72, "sex": "M", "drugs": d("와파린"), "complaint": "눈이 침침해요"}),
        ("P-14", {"age": 64, "sex": "F", "drugs": d("가바펜틴", "메트포르민"), "complaint": "피곤해요"}),
        ("P-15", {"age": 68, "sex": "M", "conditions": ["고혈압"], "drugs": d("아스피린"), "complaint": "관절이 아파요"}),
    ]
    expect = {"P-14": "needs_review", "P-06": "needs_review"}  # DB에 없는 약 / 가임기 임신 미입력
    for i, (src, patient) in enumerate(archive, start=2):
        rows.append((f"V-{i:02d}", "archive", f"archive {src}", patient, expect.get(src)))
    rows += [
        ("V-16", "금지", "와파린 + 피로(코엔자임Q10 금지)", {"age": 75, "sex": "M", "drugs": d("와파린"), "complaint": "기운이 없고 피곤해요"}, None),
        ("V-17", "금지", "저GFR(<45) + 근육경련(마그네슘 금지)", {"age": 70, "sex": "F", "exam": {"gfr": 40}, "complaint": "밤에 다리에 쥐가 나요"}, None),
        ("V-18", "금지", "메트포르민 단독(B12 고갈 보충)", {"age": 60, "sex": "M", "drugs": d("메트포르민")}, None),
        ("V-19", "금지", "스타틴(코엔자임Q10 고갈) + 와파린 동시 복용", {"age": 67, "sex": "M", "drugs": d("아토르바스타틴", "와파린"), "complaint": "근육통이 있어요"}, None),
        ("V-20", "금지", "임신 여성", {"age": 29, "sex": "F", "pregnant": True, "complaint": "잠을 잘 못 자고 피곤해요"}, None),
        ("V-21", "금지", "칼륨 보존 이뇨제", {"age": 65, "sex": "M", "drugs": d("스피로노락톤"), "complaint": "다리에 쥐가 나요"}, None),
        ("V-22", "경계", "주호소만", {"age": 45, "sex": "M", "complaint": "소화가 안 되고 속이 더부룩해요"}, None),
        ("V-23", "경계", "약만", {"age": 55, "sex": "F", "drugs": d("메트포르민", "로사르탄")}, None),
        ("V-24", "경계", "검진만", {"age": 50, "sex": "M", "exam": {"fbs": 115, "ldl": 170, "tg": 220}}, None),
        ("V-25", "경계", "정보 거의 없음(나이·성별만)", {"age": 40, "sex": "M"}, None),
        ("V-26", "경계", "규칙 없는 질환만", {"age": 60, "sex": "F", "conditions": ["건선"]}, "needs_review"),
        ("V-27", "경계", "위험 신호 주호소(흉통)", {"age": 58, "sex": "M", "complaint": "가슴이 쥐어짜듯 아프고 숨이 차요"}, "stopped"),
        ("V-28", "경계", "부정 표현 주호소", {"age": 52, "sex": "F", "complaint": "두통은 없고 요즘 피곤해요"}, None),
        ("V-29", "경계", "가임기 여성 임신 미입력", {"age": 33, "sex": "F", "complaint": "피곤하고 잠을 잘 못 자요"}, "needs_review"),
        ("V-30", "경계", "같은 이름 다중 ID 성분(홍삼 NTC-2-2·IND-2024-20, 피로/활력 분류 경로)",
         {"age": 35, "sex": "M", "complaint": "만성피로가 있고 기운이 없어요"}, None),
    ]
    return rows


def must_not(db, patient, all_ids):
    clean, errors = contract.validate(patient)
    assert clean is not None, errors
    ctx = legacy.build_context(db, clean, None)
    verdicts = legacy.safety_check(db, all_ids, ctx)
    return sorted(i for i, v in verdicts.items() if not v["allowed"])


def main():
    if OUT.exists() and "--force" not in sys.argv:
        sys.exit(f"{OUT.name}이 이미 있습니다. 정답은 커밋 후 수정하지 않습니다(--force로만 다시 생성).")
    db = graph.connect()
    all_ids = [r[0] for r in db.run(Q_ALL)]
    patients = [{"id": pid, "group": group, "note": note, "patient": patient, "expect_status": expect,
                 "must_not": must_not(db, patient, all_ids)} for pid, group, note, patient, expect in base_patients()]
    OUT.write_text(json.dumps({"patients": patients}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(patients)}명, must_not 합계 {sum(len(p['must_not']) for p in patients)}")


if __name__ == "__main__":
    main()
