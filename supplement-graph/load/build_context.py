#!/usr/bin/env python3
"""추천 로직이 쓰는 맥락 데이터를 그래프에 올린다. build_graph.py 다음에 실행한다.

    python3 load/build_context.py                 # 기본: ../data/생애주기별 질환 및 증상_통합.xlsx
    python3 load/build_context.py --xlsx <경로>

올리는 것
  - 생애주기 → 다빈도 질환 → 증상 분류·증상        (통합 엑셀 '생애주기별 통합', '증상별 정규화')
  - 세그먼트(성별 × 연령대) → 관심사 → 원료         (통합 엑셀의 남성·여성 관심사 열)
  - 질환 연령·성별 확인, 질환 → 증상, 시너지·길항   (load/refs/public_refs_kr.json, 국내 공공 출처)

통합 엑셀은 나건강 자료(기업 자료)다. 공공 출처로 확인된 부분은 관계의 ref_* / source 에 따로 적는다.
"""
import argparse
import json
import os
import pathlib
import re
import sys

import openpyxl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_graph import ROOT, load_blocks, run_block, shell, strip_comments  # noqa: E402

# 기본 위치는 ../data 다. 다른 곳에 있으면 --xlsx 나 환경변수 CONTEXT_XLSX 로 넘긴다
DEFAULT_XLSX = pathlib.Path(os.environ.get("CONTEXT_XLSX") or ROOT.parent / "data" / "생애주기별 질환 및 증상_통합.xlsx")
REFS = ROOT / "load" / "refs" / "public_refs_kr.json"
TABLE_SOURCE = "나건강 생애주기별 질환 및 증상_통합.xlsx"

# 생애주기 이름 앞부분 → 나이 범위
STAGE_AGES = {"10대미만": (0, 9), "10대": (10, 19), "20대": (20, 29), "30대": (30, 39),
              "40대": (40, 49), "50대": (50, 64), "60대 이상": (65, 200)}
# 연령대 표기 → (코드, 나이 범위)
BANDS = {"29세 이하": ("AGE_U29", 0, 29), "30~39세": ("AGE_30_39", 30, 39), "40대": ("AGE_40_49", 40, 49),
         "50~64세": ("AGE_50_64", 50, 64), "65세 이상": ("AGE_65P", 65, 200)}

# 관심사 표의 성분 이름 → 원료 ID. 표의 이름이 통칭이라 사람이 정한 대응표다.
# (ID, 대응 방식). '유사'는 같은 물질이지만 원료 범위가 표의 이름보다 좁거나 넓은 경우다.
INGREDIENT_ALIASES = {
    "마그네슘": ("NUT-45673", "이름 일치"), "칼슘": ("NUT-45672", "이름 일치"), "아연": ("NUT-45675", "이름 일치"),
    "셀레늄": ("NUT-45677", "이름 일치"), "칼륨": ("NUT-45681", "이름 일치"), "단백질": ("NUT-45684", "이름 일치"),
    "엽산": ("NUT-45668", "이름 일치"), "베타카로틴": ("NUT-45659", "이름 일치"),
    "철분": ("NUT-45674", "이름 일치"), "식이섬유소": ("NUT-45683", "이름 일치"),
    "비타민A": ("NUT-45658", "이름 일치"), "비타민C": ("NUT-45671", "이름 일치"), "비타민D": ("NUT-45660", "이름 일치"),
    "비타민E": ("NUT-45661", "이름 일치"), "비타민K": ("NUT-45662", "이름 일치"),
    "비타민B1": ("NUT-45663", "이름 일치"), "비타민B2": ("NUT-45664", "이름 일치"),
    "비타민B6": ("NUT-45667", "이름 일치"), "비타민B12": ("NUT-45669", "이름 일치"),
    "비타민B3": ("NUT-45665", "이름 일치"),   # 나이아신
    "비타민B5": ("NUT-45666", "이름 일치"),   # 판토텐산
    "비타민B7": ("NUT-45670", "이름 일치"),   # 비오틴
    "프로바이오틱스": ("NTC-2-51", "이름 일치"), "코엔자임Q10": ("NTC-2-9", "이름 일치"),
    "오메가3": ("NTC-2-16", "유사"),          # EPA 및 DHA 함유 유지
    "오메가6": ("NUT-45685", "유사"),         # 필수 지방산
    "이소플라본": ("NTC-2-10", "유사"),       # 대두이소플라본
}


def cell(v):
    return str(v).strip() if v is not None and str(v).strip() else None


def split_names(v):
    return [x.strip() for x in re.split(r"[,，]", v or "") if x.strip()]


def read_table(xlsx):
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    categories = []
    for r in wb["증상별 정규화"].iter_rows(values_only=True):
        if cell(r[1]) and cell(r[3]) and r[1] != "분류명":
            categories.append({"name": cell(r[1]), "definition": cell(r[2]), "symptoms": split_names(r[3]),
                               "source": TABLE_SOURCE + " › 증상별 정규화"})

    diseases, interests, links, unmatched = [], [], [], []
    band = stage = None
    orders = {}
    for r in wb["생애주기별 통합"].iter_rows(min_row=4, values_only=True):
        if cell(r[0]) and cell(r[0]).startswith("※"):
            break
        band = cell(r[0]) or band
        stage = cell(r[1]) or stage
        if cell(r[2]):
            name = cell(r[2])
            sex = "M" if "(남성)" in name else "F" if "(여성)" in name else "A"
            orders[stage] = orders.get(stage, 0) + 1
            diseases.append({"stage": stage, "disease": re.sub(r"\((남성|여성)\)", "", name).strip(), "sex": sex,
                             "order": orders[stage], "main_symptoms": cell(r[3]),
                             "categories": split_names(r[4]), "source": TABLE_SOURCE + " › 생애주기별 통합",
                             "band": band})
        for sex, col in (("M", 6), ("F", 10)):
            if not cell(r[col + 1]):
                continue
            code, lo, hi = BANDS[band]
            key = (sex, band)
            orders[key] = orders.get(key, 0) + 1
            interest = cell(r[col + 1])
            interests.append({"code": f"{sex}_{code}", "interest": interest, "group": cell(r[col]),
                              "order": orders[key], "source": TABLE_SOURCE,
                              "segment": {"name": f"{'남성' if sex == 'M' else '여성'} {band}", "sex": sex,
                                          "age_band": band, "age_min": lo, "age_max": hi}})
            for n, raw in enumerate(split_names(r[col + 2]), 1):
                hit = INGREDIENT_ALIASES.get(raw.replace(" ", ""))
                if hit:
                    links.append({"interest": interest, "ingredient_id": hit[0], "order": n, "raw_name": raw,
                                  "match": hit[1], "source": TABLE_SOURCE})
                else:
                    unmatched.append(f"{interest}: {raw}")
    return categories, diseases, interests, links, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    args = ap.parse_args()
    q = load_blocks()
    refs = json.loads(REFS.read_text(encoding="utf-8"))
    categories, diseases, interests, links, unmatched = read_table(args.xlsx)

    for s in strip_comments((ROOT / "cypher" / "01_schema.cypher").read_text(encoding="utf-8")).split(";"):
        if s.strip():
            shell(s.strip())

    print("생애주기")
    stages = {}
    for d in diseases:
        lo, hi = STAGE_AGES[d["stage"].split(" · ")[0]]
        stages[d["stage"]] = {"name": d["stage"], "props": {"age_band": d["band"], "age_min": lo, "age_max": hi,
                                                            "source": TABLE_SOURCE}}
    run_block(q, "upsert_life_stage", list(stages.values()))
    run_block(q, "upsert_symptom_category", categories)

    checked = {(c["stage"], c["disease"], c["sex"]): c for c in refs["common_disease"]}
    no_ref = []
    for d in diseases:
        c = checked.get((d["stage"], d["disease"], d["sex"]))
        d["props"] = {"ref_org": c["org"], "ref_doc": c.get("doc"), "ref_url": c.get("url"),
                      "ref_stat": c.get("stat")} if c else {}
        if not c:
            no_ref.append(f"{d['stage']} › {d['disease']}")
        d.pop("band")
    run_block(q, "link_common_disease", diseases)
    run_block(q, "link_disease_symptom", [
        {"disease": h["disease"], "symptom": h["symptom"], "source": h["org"],
         "props": {k: h[k] for k in ("doc", "url", "quote") if h.get(k)}} for h in refs["has_symptom"]])

    print("관심사")
    run_block(q, "upsert_segment_interest", interests)
    run_block(q, "link_interest_ingredient", links)

    print("시너지 · 길항")
    run_block(q, "upsert_synergy", [
        {"key": s["key"], "props": {"name": s["name"], "effect": s.get("effect"), "verdict": s.get("verdict"),
                                    "source": s.get("org"), "doc": s.get("doc"), "url": s.get("url"),
                                    "quote": s.get("quote")}} for s in refs["synergy"]])
    run_block(q, "link_in_synergy", [
        {"ingredient_id": e["ingredient_id"], "key": e["synergy"], "source": e.get("org"),
         "props": {k: e[k] for k in ("doc", "url", "quote", "page") if e.get(k)}} for e in refs["in_synergy"]])
    run_block(q, "link_antagonizes", [
        {"a": e["a"], "b": e["b"], "effect": e.get("effect"), "source": e.get("org"),
         "props": {k: e[k] for k in ("doc", "url", "quote") if e.get(k)}} for e in refs["antagonizes"]])

    print("\n결과")
    for r in shell("MATCH (a)-[r]->(b) WHERE type(r) IN ['COMMON_DISEASE','CLASSIFIED_AS','INCLUDES','HAS_SYMPTOM',"
                   "'HAS_INTEREST','RECOMMENDS','IN_SYNERGY','ANTAGONIZES'] "
                   "RETURN labels(a)[0] + ' -' + type(r) + '-> ' + labels(b)[0] AS rel, count(*) AS n ORDER BY rel"):
        print("  ", r)
    missing = shell("MATCH (c:SymptomCategory) RETURN sum(size([(c)-[:INCLUDES]->(s) | s]))")[0]
    print(f"   증상 분류 키워드 {sum(len(c['symptoms']) for c in categories)}개 중 그래프 증상과 이어진 것 {missing}개")
    print(f"\n관심사 성분 대응: {len(links)}건 연결, {len(unmatched)}건은 원료 목록에 없어 넣지 않았다")
    for u in unmatched:
        print("   -", u)
    if no_ref:
        print(f"\n심평원 통계 확인이 없는 생애주기 질환 {len(no_ref)}건")
        for u in no_ref:
            print("   -", u)


if __name__ == "__main__":
    main()
