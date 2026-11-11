#!/usr/bin/env python3
"""그래프 설계 검증 스크립트.

임시 Neo4j 컨테이너를 띄우고 cypher/ 파일을 적용한 뒤, 설계 문서(docs/)에 적힌 동작을
시나리오별로 확인한다. 표준 라이브러리만 쓴다. Docker 가 필요하다.

    python3 test/run_tests.py            # 실행 후 컨테이너 정리
    python3 test/run_tests.py --keep     # 끝나도 컨테이너 유지 (http://localhost:7474 로 직접 확인)
"""
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CYPHER = ROOT / "cypher"
CONTAINER = "supplement-graph-test"
IMAGE = "neo4j:5.26-community"
READY_TIMEOUT_SEC = 600


class Raw(str):
    """Cypher 식을 그대로 넣을 때 쓴다. 예: Raw('datetime()')"""


def lit(v):
    """파이썬 값을 cypher-shell 파라미터용 Cypher 리터럴로 바꾼다."""
    if isinstance(v, Raw):
        return str(v)
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(lit(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"`{k}`: {lit(x)}" for k, x in v.items()) + "}"
    raise TypeError(type(v))


def strip_comments(text):
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


def load_blocks():
    """'// @query 이름' 으로 시작해 ';' 로 끝나는 블록을 모은다."""
    blocks = {}
    for path in sorted(CYPHER.glob("*.cypher")):
        name, buf = None, []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*//\s*@query\s+(\w+)", line)
            if m:
                name, buf = m.group(1), []
                continue
            if name is None or line.strip().startswith("//"):
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                blocks[name] = "\n".join(buf).rstrip().rstrip(";")
                name = None
    return blocks


def shell(stmt, params=None):
    cmd = ["docker", "exec", "-i", CONTAINER, "cypher-shell", "--format", "plain"]
    if params:
        cmd += ["-P", lit(params)]
    r = subprocess.run(cmd, input=stmt, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cypher 실패:\n{stmt[:300]}\n---\n{r.stderr.strip()}")
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return lines[1:]  # 첫 줄은 컬럼명


def apply_file(name):
    stmts = [s.strip() for s in strip_comments((CYPHER / name).read_text(encoding="utf-8")).split(";")]
    for s in stmts:
        if s:
            shell(s)


def start(keep):
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    ports = ["-p", "7474:7474", "-p", "7687:7687"] if keep else []
    subprocess.run(["docker", "run", "-d", "--rm", "--name", CONTAINER, *ports,
                    "-e", "NEO4J_AUTH=none", IMAGE], check=True, capture_output=True)
    deadline = time.time() + READY_TIMEOUT_SEC
    while time.time() < deadline:
        r = subprocess.run(["docker", "exec", CONTAINER, "cypher-shell", "RETURN 1"], capture_output=True)
        if r.returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError("Neo4j 가 제시간에 뜨지 않았다")


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    keep = "--keep" in sys.argv
    q = load_blocks()
    print(f"쿼리 블록 {len(q)}개 로드: {', '.join(sorted(q))}\n")
    start(keep)
    try:
        # ---------- 스키마 · 샘플 ----------
        apply_file("01_schema.cypher")
        apply_file("01_schema.cypher")
        check("스키마를 두 번 적용해도 에러 없음", True)
        apply_file("02_seed_sample.cypher")
        shell("CALL db.awaitIndexes(60)")

        # ---------- 조회 ----------
        rows = shell(q["symptom_fulltext"], {"q": "피곤"})
        check("전문검색: '피곤' → 만성피로 (별칭 '계속 피곤함')", rows and "SYM-fatigue-chronic" in rows[0], rows)

        rows = shell(q["symptom_to_ingredient"], {"symptom_ids": ["SYM-fatigue-chronic"]})
        ids = " ".join(rows)
        check("증상→원료: 만성피로 → 비타민 B1·B12", "NUT-45663" in ids and "NUT-45669" in ids, rows)

        rows = shell(q["name_lookup"], {"q": "IMO"})
        ids = " ".join(rows)
        check("이름 검색: 물질 별칭 'IMO' → 개별인정형 2건 모두", "IND-2005-10" in ids and "IND-2005-11" in ids, rows)

        rows = shell(q["condition_to_ingredient"], {"condition_codes": ["h-3"]})
        check("조건→원료: h-3 → 녹차추출물(고시형)", rows and "NTC-green-tea" in rows[0], rows)

        rows = shell(q["drug_depletes"], {"drug_ids": ["DRUG-metformin"]})
        check("약물 고갈: 메트포르민 → 비타민 B12", rows and "NUT-45669" in rows[0], rows)

        rows = shell(q["safety_filter"], {"ingredient_ids": ["IND-2004-6", "NUT-45663"],
                                          "condition_codes": [], "drug_ids": ["DRUG-metformin"]})
        dang = next((r for r in rows if "IND-2004-6" in r), "")
        b1 = next((r for r in rows if "NUT-45663" in r), "")
        check("안전 필터: 메트포르민(성분) → 혈당강하제(계열) 경유로 참당귀 차단",
              dang.endswith("FALSE") and "혈당강하제" in dang, rows)
        check("안전 필터: 같은 조건에서 비타민 B1 은 통과", b1.endswith("TRUE"), rows)

        rows = shell(q["safety_filter"], {"ingredient_ids": ["IND-2004-7"],
                                          "condition_codes": ["POP-pregnant"], "drug_ids": []})
        check("안전 필터: 임산부 조건 → 히비스커스 복합추출물 차단 (약 없음)", rows and rows[0].endswith("FALSE"), rows)

        rows = shell(q["evidence_pack"], {"ingredient_ids": ["NUT-45663"]})
        check("근거 묶음: 섭취량 수치와 기능성 포함", rows and "0.36" in rows[0] and "대사 및 에너지 생성" in rows[0], rows)

        # ---------- 개별인정형 동기화 ----------
        new_row = {"ingredient_id": "IND-2025-64", "source_hash": "n1",
                   "api": {"name": "마르멜로추출물", "recognition_no": "2025-64", "intake_min": 1500,
                           "intake_max": 1500, "intake_unit": "mg", "source": "I-0050",
                           "function_text": "체지방 감소에 도움을 줄 수 있음"}}
        shell(q["upsert_individual"], {"rows": [new_row]})
        rows = shell("MATCH (i:Ingredient {ingredient_id:'IND-2025-64'}) RETURN labels(i), i.status, i.review_state")
        check("동기화: 신규 원료는 :Individual, active, unreviewed 로 생성",
              rows and "Individual" in rows[0] and "active" in rows[0] and "unreviewed" in rows[0], rows)
        rows = shell(q["review_queue"])
        check("동기화: 신규 원료가 검수 목록에 뜸", any("IND-2025-64" in r for r in rows), rows)

        before = shell("MATCH (i:Ingredient {ingredient_id:'IND-2025-64'}) RETURN toString(i.synced_at)")
        rows = shell(q["upsert_individual"], {"rows": [new_row]})
        after = shell("MATCH (i:Ingredient {ingredient_id:'IND-2025-64'}) RETURN toString(i.synced_at)")
        check("동기화: 같은 해시로 재실행하면 API 필드 갱신 없음", before == after and rows == ["0"], (before, after, rows))

        rows = shell(q["symptom_to_ingredient"], {"symptom_ids": ["SYM-constipation"]})
        ids = " ".join(rows)
        check("변경 전: 변비 → 이소말토올리고당 2건 추천", "IND-2005-10" in ids and "IND-2005-11" in ids, rows)

        changed = {"ingredient_id": "IND-2005-10", "source_hash": "changed",
                   "api": {"name": "이소말토올리고당", "recognition_no": "2005-10", "intake_min": 8,
                           "intake_max": 15, "intake_unit": "g", "source": "I-0050",
                           "function_text": "장내 유익균 증식, 배변활동 원활에 도움을 줄 수 있음"}}
        rows = shell(q["upsert_individual"], {"rows": [changed]})
        check("동기화: 기능성 원문 변경 → 1건 검수 필요 표시", rows == ["1"], rows)
        rows = shell("MATCH (i:Ingredient {ingredient_id:'IND-2005-10'}) "
                     "OPTIONAL MATCH (i)-[f:HAS_FUNCTION]->(t) OPTIONAL MATCH (i)-[:FORM_OF]->(s) "
                     "RETURN i.intake_max, i.review_state, f.needs_review, t.tag_id, s.substance_id")
        r = rows[0] if rows else ""
        check("동기화: 섭취량은 API 값으로 갱신(15)", r.startswith("15"), rows)
        check("동기화: 사람이 만든 기능성 관계·물질 묶음은 유지", "TAG-gut" in r and "SUB-imo" in r, rows)
        check("동기화: 원료와 기능성 관계에 needs_review 표시", "needs_review" in r and "TRUE" in r, rows)

        rows = shell(q["symptom_to_ingredient"], {"symptom_ids": ["SYM-constipation"]})
        ids = " ".join(rows)
        check("변경 후: 검수 전인 IND-2005-10 은 추천 경로에서 빠지고 IND-2005-11 은 유지",
              "IND-2005-10" not in ids and "IND-2005-11" in ids, rows)

        shell(q["mark_reviewed"], {"ingredient_id": "IND-2005-10"})
        rows = shell(q["symptom_to_ingredient"], {"symptom_ids": ["SYM-constipation"]})
        check("검수 완료 후: IND-2005-10 이 추천 경로에 복귀", "IND-2005-10" in " ".join(rows), rows)

        # 월간 전체 수집: IND-2005-11 만 이번 수집에서 빠졌다고 가정
        t0 = shell("RETURN toString(datetime())")[0].strip('"')
        time.sleep(1)
        seen = [new_row, changed] + [
            {"ingredient_id": i, "source_hash": "seed", "api": {}} for i in ["IND-2004-6", "IND-2004-7"]]
        shell(q["upsert_individual"], {"rows": seen})
        rows = shell(q["mark_missing_individual"], {"run_started_at": Raw(f'datetime("{t0}")')})
        check("누락 감지: 전체 수집에 없던 IND-2005-11 만 표시", rows and "IND-2005-11" in rows[0]
              and "IND-2005-10" not in rows[0], rows)
        rows = shell("MATCH (i:Ingredient {ingredient_id:'IND-2005-11'}) RETURN i.status")
        check("누락 감지: 삭제·비활성화하지 않고 active 유지", rows == ['"active"'], rows)

        shell(q["upsert_individual"], {"rows": [{"ingredient_id": "IND-2005-11", "source_hash": "seed", "api": {}}]})
        rows = shell("MATCH (i:Ingredient {ingredient_id:'IND-2005-11'}) RETURN i.missing_since IS NULL")
        check("누락 해제: 다음 수집에 다시 보이면 missing_since 해제 (해시가 같아도)", rows == ["TRUE"], rows)

        shell(q["revoke_individual"], {"ingredient_id": "IND-2005-11", "note": "인정 취소 확인"})
        rows = shell(q["name_lookup"], {"q": "IMO"})
        ids = " ".join(rows)
        check("취소: revoked 원료는 이름 검색에서 빠짐", "IND-2005-11" not in ids and "IND-2005-10" in ids, rows)

        shell(q["upsert_curated_ingredient"], {"rows": [{"ingredient_id": "NTC-imo", "kind": "Notice",
                                                          "source_hash": "c1",
                                                          "props": {"name": "이소말토올리고당", "source": "I2710"}}]})
        rows = shell(q["convert_to_notice"], {"old_id": "IND-2005-10", "new_id": "NTC-imo",
                                              "converted_at": "2026-11-10"})
        check("고시형 전환: 이전 노드 converted", rows and '"converted"' in rows[0], rows)
        rows = shell(q["name_lookup"], {"q": "IMO"})
        ids = " ".join(rows)
        check("고시형 전환: 물질 별칭 검색이 새 고시형 노드로 이어짐", "NTC-imo" in ids and "IND-2005-10" not in ids, rows)
    finally:
        if keep:
            print(f"\n컨테이너 유지: {CONTAINER} (http://localhost:7474, 인증 없음)")
        else:
            subprocess.run(["docker", "stop", CONTAINER], capture_output=True)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n결과: {len(RESULTS) - len(failed)}/{len(RESULTS)} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
