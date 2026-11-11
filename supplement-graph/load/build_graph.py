#!/usr/bin/env python3
"""통합 엑셀을 설계 스키마(docs/DESIGN.md)대로 Neo4j 에 적재한다.

    docker compose --env-file .neo4j.env up -d
    python3 load/build_graph.py                    # 기본 엑셀: ../data/건기식원료_통합_…_261110.xlsx
    python3 load/build_graph.py --xlsx <경로>      # 다른 엑셀
    python3 load/build_graph.py --verify-only      # 적재 없이 검증만

쿼리는 cypher/ 의 '// @query' 블록만 쓴다. MERGE 기반이라 다시 실행해도 안전하다.
엑셀에 없는 것은 만들지 않는다. 예외는 안전 관계 하나다: 주의사항 문구에서 키워드로
뽑아 EXCLUDES / INTERACTS_WITH 를 만들고, evidence='hypothesis', derived=true 로 표시한다.
"""
import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
from run_tests import lit, load_blocks, strip_comments  # noqa: E402

CONTAINER = "supplement-graph-neo4j"
DEFAULT_XLSX = ROOT.parent / "data" / "건기식원료_통합_영양성분_고시형_개별인정형_261110.xlsx"
ENV_FILE = ROOT / ".neo4j.env"
SAFETY_SOURCE = "주의사항 문구(키워드 추출)"

# ---------------------------------------------------------------- Neo4j 호출


def password():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("NEO4J_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{ENV_FILE} 에 NEO4J_PASSWORD 가 없다")


ENV = None


def shell(stmt, params=None):
    global ENV
    if ENV is None:
        ENV = dict(os.environ, NEO4J_USERNAME="neo4j", NEO4J_PASSWORD=password())
    cmd = ["docker", "exec", "-i", "-e", "NEO4J_USERNAME", "-e", "NEO4J_PASSWORD",
           CONTAINER, "cypher-shell", "--format", "plain"]
    if params:
        cmd += ["-P", lit(params)]
    r = subprocess.run(cmd, input=stmt, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise RuntimeError(f"cypher 실패:\n{stmt[:300]}\n---\n{r.stderr.strip()[:2000]}")
    return [l for l in r.stdout.splitlines() if l.strip()][1:]


def run_block(q, name, rows, batch=100):
    total = 0
    for i in range(0, len(rows), batch):
        out = shell(q[name], {"rows": rows[i:i + batch]})
        total += int(out[0]) if out and out[0].isdigit() else 0
    print(f"  {name:<28} {len(rows):>5}행")
    return total


# ---------------------------------------------------------------- 정규화


def clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v if v and v != "-" else None


def sha(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


RECOG_SUFFIX = re.compile(r"\((?:[^()]|\([^()]*\))*제\s*\d{4}-\d+호\)\s*$")


def clean_name(raw):
    """'핑거루트추출물(판두라틴)(제2013-5호)' → '핑거루트추출물(판두라틴)'. 업체·인정번호 꼬리표만 뗀다."""
    name = RECOG_SUFFIX.sub("", raw).strip()
    return name or raw


def parse_amount(v):
    """'2,400 (mg)' → 2400. 숫자가 없으면 None."""
    m = re.match(r"\s*([\d,]+(?:\.\d+)?)", str(v or ""))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n) if n.is_integer() else n


def split_function(text):
    """'(국문) … (영문) …' → (국문, 영문)."""
    text = clean(text)
    if not text:
        return None, None
    m = re.search(r"\(영문\)", text)
    ko, en = (text[:m.start()], text[m.end():]) if m else (text, None)
    ko = re.sub(r"^\s*\(국문\)\s*", "", ko).strip()
    return ko or None, (en.strip() or None) if en else None


def drop_none(d):
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------- 안전 관계 추출 규칙

# (code, label, 정규식)
POPULATION_RULES = [
    ("POP-pregnant", "임산부", r"임산부|임신"),
    ("POP-lactating", "수유부", r"수유"),
    ("POP-infant", "영·유아", r"영\s?[·‧ㆍ･]?\s?유아"),
    ("POP-child", "어린이·청소년", r"어린이|소아|청소년"),
    ("POP-allergy", "알레르기 체질", r"알레르기|알러지|과민반응"),
]
# 질환 조건. 약 이름("고혈압치료제")이 아니라 질환 자체를 가리키는 절만 잡는다
DISEASE_RULES = [
    ("DIS-diabetes", "당뇨병·고혈당", r"당뇨병|당뇨\s?환자|높은 혈당|당뇨,|당뇨 및|\(당뇨"),
    ("DIS-hypertension", "고혈압·혈압 이상", r"고혈압(?!\s?치료제)|혈압이 낮|, 혈압 등의 질환"),
    ("DIS-kidney", "신장 질환", r"신장|신부전"),
    ("DIS-liver", "간 질환", r"간\s?질환|간[･·,]\s?심장질환|위, 간 및"),
]
# (drug_id, 이름, 정규식)
DRUG_RULES = [
    ("CLS-anticoagulant", "항응고제·항혈소판제",
     r"항응고|혈액\s?응고\s?(?:방지|저해)?제|혈액응고 관련 의약품|와파린|쿠마딘|항혈전|항혈소판|아스피린"),
    ("CLS-antidiabetic", "혈당강하제", r"당뇨\s?병?\s?치료제|혈당강하제|항당뇨제|당뇨약|당뇨병 치료를 받"),
    ("CLS-antihypertensive", "혈압강하제", r"고혈압\s?치료제|혈압강하제|혈압약|혈압조절제"),
    ("CLS-antidepressant", "항우울제", r"항우울제"),
    ("CLS-immunosuppressant", "면역억제제", r"면역억제제"),
    ("CLS-hormone", "호르몬제", r"호르몬제"),
    ("CLS-sedative", "수면제·신경안정제", r"신경\s?안정제|수면유도제|중추신경억제제"),
    ("CLS-any", "의약품 일반(복용 중인 약이 있으면 해당)", r"의약품|약물|약제"),
]
AVOID = re.compile(r"피할 것|피하|삼가|하지\s?말|마십시오|금지")
# 보관·지도 문구라 대상군 제외가 아니다
NOT_EXCLUSION = re.compile(r"함부로 섭취하지 않도록")
CLAUSE_SPLIT = re.compile(r"\n|\(\d+\)|\([가-힣]\)|[①-⑩]|(?<=[것음다])\.\s|\s-\s")


def clauses(text):
    for c in CLAUSE_SPLIT.split(text or ""):
        c = re.sub(r"^\s*[-•·]\s*", "", c).strip(" .")
        if c:
            yield c


def derive_safety(ingredient_id, precaution):
    """주의사항 문구 → (EXCLUDES 행, INTERACTS_WITH 행). 같은 대상이 여러 번 나오면 가장 센 강도를 남긴다."""
    ex, ix = {}, {}
    for c in clauses(precaution):
        severity = "avoid" if AVOID.search(c) else "caution"
        if not NOT_EXCLUSION.search(c):
            for code, _, pat in POPULATION_RULES + DISEASE_RULES:
                if re.search(pat, c) and (code not in ex or severity == "avoid"):
                    ex[code] = (severity, c)
        matched = [d for d, _, pat in DRUG_RULES if re.search(pat, c)]
        if len(matched) > 1:  # 특정 계열이 잡힌 절은 '의약품 일반'으로 넓히지 않는다
            matched = [d for d in matched if d != "CLS-any"]
        for drug_id in matched:
            if drug_id not in ix or severity == "avoid":
                ix[drug_id] = (severity, c)
    props = {"evidence": "hypothesis", "derived": True}
    excludes = [{"code": k, "ingredient_id": ingredient_id, "rel": "EXCLUDES", "priority": None,
                 "reason": c, "source": SAFETY_SOURCE, "props": dict(props, severity=s)}
                for k, (s, c) in ex.items()]
    interacts = [{"ingredient_id": ingredient_id, "drug_id": k, "severity": s, "note": c,
                  "source": SAFETY_SOURCE, "props": props} for k, (s, c) in ix.items()]
    return excludes, interacts


# ---------------------------------------------------------------- 엑셀 → 행


def read(xlsx):
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    sheet = lambda n: list(wb[n].iter_rows(values_only=True))  # noqa: E731
    return {"ing": [r for r in sheet("통합(기준)")[1:] if r[0]],
            "path": [r for r in sheet("경로(성분-기능성-기전-증상)")[2:] if r[2]],
            "tag": [r for r in sheet("기능성 태그")[1:] if r[0]],
            "mech": [r for r in sheet("기전")[1:] if r[0]],
            "sym": [r for r in sheet("증상")[1:] if r[0]]}


def build_ingredients(rows, notes):
    """통합 시트 → (큐레이션 원료 행, 개별인정형 행, 업체 행). 같은 인정번호는 API 행을 쓴다."""
    by_id = collections.OrderedDict()
    for r in rows:
        by_id.setdefault(r[0], []).append(r)
    curated, individual, companies = [], [], []
    for iid, group in by_id.items():
        api = [r for r in group if r[1] == "개별인정형(API)"]
        crawl = [r for r in group if r[1] == "개별인정형(크롤링)"]
        for r in crawl:
            name = clean(r[20])
            if name:
                d = re.search(r"\((\d{4})\.(\d{1,2})\.(\d{1,2})\.?\)", str(r[2]))
                companies.append({"ingredient_id": iid, "company_name": name,
                                  "company_id": "CO-" + hashlib.sha1(name.encode()).hexdigest()[:10],
                                  "recognized_at": f"{d[1]}-{int(d[2]):02d}-{int(d[3]):02d}" if d else None})
        r = (api or group)[0]
        if len(api) > 1:
            notes.append(f"{iid}: 같은 인정번호의 API 행이 {len(api)}개다. 첫 행을 썼다 "
                         f"(섭취량 상한 {' / '.join(str(x[14]) for x in api)})")
        kind = r[1]
        if kind in ("영양성분", "고시형"):
            props = drop_none({
                "name": clean(r[3]), "intake_raw": clean(r[5]), "function_text": clean(r[4]),
                "precaution_text": clean(r[6]), "source": str(r[22]).rsplit(" ", 1)[0],
                "origin_id": clean(r[2]), "nutrient_type": clean(r[8]), "manufacturing_std": clean(r[7]),
                "raw_materials": clean(r[10]), "marker_compound": clean(r[11])})
            curated.append({"ingredient_id": iid, "kind": "Nutrient" if kind == "영양성분" else "Notice",
                            "source_hash": sha(r), "props": props})
            continue
        ko, en = split_function(r[4])
        raw_name = clean(r[3])
        api_props = {"name": clean_name(raw_name), "name_raw": raw_name,
                     "recognition_no": iid[len("IND-"):], "function_text": ko, "function_text_en": en,
                     "precaution_text": clean(r[6])}
        if api:
            lo, hi = clean(r[15]), clean(r[14])
            api_props.update({
                "intake_min": parse_amount(lo), "intake_max": parse_amount(hi),
                "intake_unit": re.sub(r"/일?$", "", clean(r[16]) or "") or None,
                "intake_raw": lo if lo == hi else f"{lo} ~ {hi}", "source": "I-0050"})
            if len(api) > 1:
                api_props["source_conflict"] = True
        else:
            # 크롤링 문구는 '○○로서 1,500 mg/일' 같은 문장이라 숫자를 뽑지 않고 원문만 둔다
            api_props.update({"intake_raw": clean(r[5]), "source": "크롤링(2-3. 개별인정형)"})
        individual.append({"ingredient_id": iid, "source_hash": sha(r), "api": drop_none(api_props)})
    return curated, individual, companies


def uniq(values):
    out = []
    for v in values:
        v = clean(v)
        if v and v not in out:
            out.append(v)
    return out


def build_edges(paths):
    """경로 시트 → 관계별 유일 쌍. 근거 열은 그 관계에 속한 것만 모은다."""
    it, tm, ms, mi = (collections.defaultdict(list) for _ in range(4))
    for r in paths:
        if r[0].startswith("끊김: 공공 출처 없어 삭제"):
            continue
        ing, tag, mech, sym = r[2], r[5], r[7], r[9]
        if tag:
            it[(ing, tag)].append(r)
        if tag and mech:
            tm[(mech, tag)].append(r)
        if mech and sym:
            ms[(sym, mech)].append(r)
        if not tag and mech and r[0] == "연결":
            mi[(mech, ing)].append(r)

    def evidence_of(verdicts):
        return "confirmed" if any(v in ("일치", "근거 있음") for v in verdicts) else "hypothesis"

    has_function = []
    for (ing, tag), rs in it.items():
        verdicts = uniq(r[14] for r in rs)
        has_function.append({
            "ingredient_id": ing, "tag_id": tag, "text": ", ".join(uniq(r[11] for r in rs)),
            "source": "; ".join(uniq(r[12] for r in rs)), "evidence": evidence_of(verdicts),
            "props": {"verdict": "일치" if "일치" in verdicts else (verdicts[0] if verdicts else None),
                      "docs": uniq(r[13] for r in rs)}})

    def ev(rs, org, doc, url, quote, verdict=None):
        verdicts = uniq(r[verdict] for r in rs) if verdict is not None else []
        return ("; ".join(uniq(r[org] for r in rs)),
                evidence_of(verdicts) if verdicts else "confirmed",
                drop_none({"verdict": ("근거 있음" if "근거 있음" in verdicts else verdicts[0]) if verdicts else None,
                           "source_orgs": uniq(r[org] for r in rs), "docs": uniq(r[doc] for r in rs),
                           "urls": uniq(r[url] for r in rs), "quote": " / ".join(uniq(r[quote] for r in rs)[:3]) or None}))

    mech_tag, sym_mech, replenished = [], [], []
    for (mech, tag), rs in tm.items():
        s, e, p = ev(rs, 15, 16, 17, 22)
        mech_tag.append({"mechanism_id": mech, "tag_id": tag, "source": s, "evidence": e, "props": p})
    for (sym, mech), rs in ms.items():
        s, e, p = ev(rs, 19, 20, 21, 23, verdict=18)
        sym_mech.append({"symptom_id": sym, "mechanism_id": mech, "source": s, "evidence": e, "props": p})
    for (mech, ing), rs in mi.items():
        s, e, p = ev(rs, 15, 16, 17, 22)
        p["verdict"] = "결핍 기전 근거 상속"
        replenished.append({"mechanism_id": mech, "ingredient_id": ing, "source": s, "evidence": e, "props": p})
    return has_function, mech_tag, sym_mech, replenished


# ---------------------------------------------------------------- 적재 · 검증


def load(data, q):
    notes = []
    curated, individual, companies = build_ingredients(data["ing"], notes)
    has_function, mech_tag, sym_mech, replenished = build_edges(data["path"])

    print("스키마")
    for s in strip_comments((ROOT / "cypher" / "01_schema.cypher").read_text(encoding="utf-8")).split(";"):
        if s.strip():
            shell(s.strip())

    print("노드")
    run_block(q, "upsert_curated_ingredient", curated, 25)
    run_block(q, "upsert_individual", individual, 25)
    run_block(q, "upsert_function_tag", [
        {"tag_id": r[0], "props": drop_none({"name": clean(r[1]), "source": clean(r[3]),
                                             "phrases": [p.strip() for p in str(r[2] or "").split(",") if p.strip()]})}
        for r in data["tag"]])
    kept = [r for r in data["mech"] if r[5] == "유지"]
    notes += [f"기전 {r[0]} {r[2]}: 엑셀에서 '{r[5]}' 로 표시되어 넣지 않았다" for r in data["mech"] if r[5] != "유지"]
    run_block(q, "upsert_mechanism", [
        {"mechanism_id": r[0], "props": drop_none({"name": clean(r[2]), "category": clean(r[1]),
                                                   "handling": clean(r[3]), "source": clean(r[4])})} for r in kept])
    run_block(q, "upsert_symptom", [
        {"symptom_id": r[0], "props": drop_none({"name": clean(r[1]), "source": clean(r[2])})}
        for r in data["sym"] if r[3] == "유지"])

    print("설명 경로")
    run_block(q, "link_has_function", has_function)
    run_block(q, "link_mechanism_tag", mech_tag)
    run_block(q, "link_symptom_mechanism", sym_mech)
    run_block(q, "link_mechanism_replenished", replenished)

    print("업체")
    run_block(q, "upsert_company_recognition", companies)

    print("안전 관계 (주의사항 문구에서 추출)")
    precautions = {row["ingredient_id"]: row["props"].get("precaution_text") for row in curated}
    precautions.update({row["ingredient_id"]: row["api"].get("precaution_text") for row in individual})
    excludes, interacts = [], []
    for iid, text in precautions.items():
        e, i = derive_safety(iid, text)
        excludes += e
        interacts += i
    run_block(q, "upsert_condition", [{"code": c, "props": {"kind": "population", "label": label}}
                                      for c, label, _ in POPULATION_RULES] +
              [{"code": c, "props": {"kind": "disease", "label": label}} for c, label, _ in DISEASE_RULES])
    run_block(q, "upsert_drug", [{"drug_id": d, "class_id": None, "source": None,
                                  "props": {"name": name, "level": "class"}} for d, name, _ in DRUG_RULES])
    # 키워드로 뽑은 관계는 규칙이 바뀌면 다시 만든다. 사람이 넣은 관계(derived 없음)는 건드리지 않는다
    shell("MATCH ()-[r:EXCLUDES|INTERACTS_WITH]->() WHERE r.derived = true DELETE r")
    run_block(q, "link_condition", excludes)
    run_block(q, "link_interacts_with", interacts)
    shell("CALL db.awaitIndexes(120)")
    return notes


def verify(data):
    """엑셀의 '연결' 경로와 그래프를 탐색해 나온 경로가 같은지 원료별로 비교한다."""
    print("\n검증")
    ok = True
    excel = collections.Counter()
    for ing, tag, mech, sym in {(r[2], r[5], r[7], r[9]) for r in data["path"] if r[0] == "연결"}:
        excel[ing] += 1
    rows = shell("""
MATCH (s:Symptom)-[:EXPLAINED_BY]->(m:Mechanism)
CALL { WITH m MATCH (m)-[:SUPPORTED_BY]->(t:FunctionTag)<-[:HAS_FUNCTION]-(i:Ingredient) RETURN i, t.tag_id AS tag
       UNION WITH m MATCH (m)-[:REPLENISHED_BY]->(i:Ingredient) RETURN i, null AS tag }
RETURN i.ingredient_id, count(*)""")
    graph = {a.strip('"'): int(b) for a, b in (r.split(", ") for r in rows)}
    diff = {k: (excel.get(k, 0), graph.get(k, 0)) for k in set(excel) | set(graph) if excel.get(k, 0) != graph.get(k, 0)}
    print(f"  설명 경로: 엑셀 {sum(excel.values())}개 / 그래프 {sum(graph.values())}개, "
          f"연결된 원료 엑셀 {len(excel)}개 / 그래프 {len(graph)}개, 불일치 원료 {len(diff)}개")
    if diff:
        ok = False
        for k, (e, g) in sorted(diff.items())[:10]:
            print(f"    {k}: 엑셀 {e} / 그래프 {g}")

    ids = {r[0] for r in data["ing"]}
    n = int(shell("MATCH (i:Ingredient) RETURN count(i)")[0])
    print(f"  원료: 엑셀 유일 ID {len(ids)}개 / 그래프 {n}개")
    ok &= n == len(ids)

    print("  노드")
    for r in shell("MATCH (n) RETURN [l IN labels(n) WHERE l <> 'Ingredient'][0] + "
                   "CASE WHEN n:Ingredient THEN ' (Ingredient)' ELSE '' END AS label, count(*) AS n ORDER BY n DESC"):
        print("   ", r)
    print("  관계")
    for r in shell("MATCH (a)-[r]->(b) RETURN labels(a)[0] + ' -' + type(r) + '-> ' + labels(b)[0] AS rel, "
                   "count(*) AS n ORDER BY n DESC"):
        print("   ", r)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    data = read(args.xlsx)
    notes = [] if args.verify_only else load(data, load_blocks())
    ok = verify(data)
    if notes:
        print("\n확인할 것")
        for n in notes:
            print("  -", n)
    print("\n결과:", "엑셀과 그래프가 일치" if ok else "불일치 있음")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
