"""증상-기전 공식 근거 + 기전-성분(식약처 기능성 문구) 엑셀 생성."""
import collections
import json
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SP = Path(__file__).parent  # links.json 위치
EV = SP  # results/, sources/, ods_overlay.json, mech_ingredient.json
OUTS = [Path(p) for p in sys.argv[1:]]
MT = Path("/Users/user/Documents/projects/wellcheck/data/mechanism_table.xlsx")
TODAY = "2026-11-09"

links = json.load(open(SP / "links.json"))
mt = openpyxl.load_workbook(MT, read_only=True)
mech = {r[0]: {"group": r[1], "name": r[2], "definition": r[4]}
        for r in list(mt["기전 체계"].iter_rows(values_only=True))[1:] if r[0]}


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


# 결과 합치기 + 인용 대조
sources, results = {}, collections.defaultdict(list)
for g in "ABCDE":
    data = json.load(open(EV / "results" / f"group_{g}.json"))
    for s in data["sources"]:
        sid = s["source_id"]
        if sid in sources and sources[sid]["url"] != s["url"]:
            print("source_id 충돌", sid, file=sys.stderr)
        sources[sid] = {**s, "group": g}
    for r in data["links"]:
        results[(r["symptom"], r["mech_id"], r["level"])].append({**r, "group": g})

ods = json.load(open(EV / "ods_overlay.json"))
for s in ods["sources"]:
    sources[s["source_id"]] = {**s, "group": "ODS"}
for u in ods["upgrades"]:
    for key in [k for k in results if k[0] == u["symptom"] and k[1] == u["mech_id"]]:
        for r in results[key]:
            r.update(status="supported", source_id=u["source_id"], quote=u["quote"], quote_ko=u["quote_ko"],
                     note=f"[ODS 팩트시트로 보강] {r.get('note', '')}")
for key, rs in results.items():
    for r in rs:
        if key[1] in ods["checked_no_support"] and r["status"] != "supported":
            r["note"] = f"[{ods['checked_no_support'][key[1]]}] {r.get('note', '')}"

texts = {}
for sid, s in sources.items():
    f = EV / s.get("file", f"sources/{sid}.txt")
    texts[sid] = norm(f.read_text(errors="ignore")) if f.exists() else None

# 그룹 E 보고: 원인 문장과 증상 문장이 다른 문단 → 엄격 기준으로 partial
CROSS = {("잦은 배뇨", "H2"), ("관절 소리", "S2"), ("불안", "H1"), ("기분 저하", "H1"), ("신경과민", "H1"), ("예민함", "H1")}

rows, used = [], collections.Counter()
for l in links:
    mid = l["mech"].split()[0]
    r = results[(l["symptom"], mid, l["level"])].pop(0)
    status, note, verify = r["status"], r.get("note", ""), ""
    if status in ("supported", "partial"):
        t = texts.get(r.get("source_id"))
        if t is None:
            verify = "실패: 원문 파일 없음"
        elif norm(r.get("quote")) and norm(r["quote"]) in t:
            verify = "통과"
        else:
            verify = "실패: 인용이 원문에 없음"
        if verify != "통과":
            status, note = "not_found", f"[인용 검증 실패로 근거 없음 처리] {note}"
        else:
            used[r["source_id"]] += 1
    if status == "supported" and (l["symptom"], mid) in CROSS:
        status, note = "partial", f"[원인·증상 문장이 다른 문단 → 부분 근거로 낮춤] {note}"
    s = sources.get(r.get("source_id")) if status != "not_found" else None
    rows.append({"cat": l["cat"], "symptom": l["symptom"], "mid": mid, "mech": mech[mid]["name"], "level": l["level"],
                 "status": status, "org": s["org"] if s else "", "title": s["title"] if s else "",
                 "url": s["url"] if s else "", "quote": r.get("quote", "") if s else "",
                 "quote_ko": r.get("quote_ko", "") if s else "", "verify": verify, "note": note,
                 "source_id": r.get("source_id", "") if s else ""})

LABEL = {"supported": "근거 있음", "partial": "부분 근거", "not_found": "공식 근거 없음"}
FILL = {"supported": "E2F0D9", "partial": "FFF2CC", "not_found": "F8CBAD"}

mi = json.load(open(EV / "mech_ingredient.json"))
mi_by = collections.defaultdict(list)
for o in mi:
    mi_by[o["mech_id"]].append(o)

wb = openpyxl.Workbook()
H = Font(bold=True)


def sheet(title, header, data, widths, status_col=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font = H
        c.fill = PatternFill("solid", fgColor="D9E1F2")
    for d in data:
        ws.append(d)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        if status_col is not None:
            key = next((k for k, v in LABEL.items() if v == row[status_col].value), None)
            if key:
                row[status_col].fill = PatternFill("solid", fgColor=FILL[key])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


# 읽는 법
guide = wb.active
guide.title = "읽는 법"
tot = collections.Counter(r["status"] for r in rows)
prim = collections.Counter(r["status"] for r in rows if r["level"] == "1차")
for line in [
    ["항목", "설명"],
    ["이 파일", f"증상 → 기전 → 성분 연결마다 공식 출처를 붙인 표. 작성 {TODAY}. 약사 검토 전."],
    ["범위", "「생애주기별 질환 및 증상_통합.xlsx」 증상별 정규화 시트의 14개 대분류, 키워드 71개 전부. "
             "기전 초안은 wellcheck/data/mechanism_table.xlsx(37개 기전, 근거 없는 Claude 초안)."],
    ["증상-기전 결과", f"연결 {len(rows)}건(1차 {sum(prim.values())}, 2차 {len(rows) - sum(prim.values())}). "
                    f"근거 있음 {tot['supported']}, 부분 근거 {tot['partial']}, 공식 근거 없음 {tot['not_found']}. "
                    f"1차만: 근거 있음 {prim['supported']}, 부분 {prim['partial']}, 없음 {prim['not_found']}."],
    ["허용 출처", "공식 기관만: 식약처, 질병관리청, 보건복지부, 미국 NIH(MedlinePlus·NIDDK·NHLBI·NCCIH·NIAMS·NEI·NIA·NINDS·ODS), CDC, WHO. "
               "대학병원·제약사·위키·뉴스·MSD 매뉴얼은 쓰지 않음. 이번 결과의 출처는 모두 미국 NIH 계열."],
    ["판정 기준", "근거 있음: 한 출처 안에서 증상과 기전(원인·결핍·생리)을 잇는 문장이 있음. "
               "부분 근거: 질환 단위로만 언급, 증상 범위가 다름, 원인 문장과 증상 문장이 다른 문단. "
               "공식 근거 없음: 허용 출처에서 잇는 문장을 찾지 못함(추측으로 채우지 않음)."],
    ["인용 검증", "인용은 출처 원문을 그대로 복사한 것이며, 저장한 원문 텍스트에 공백만 정리해 포함되는지 코드로 대조함. 실패하면 '공식 근거 없음'으로 처리."],
    ["공식 근거 없음의 원인", "① 초안 기전이 공식 자료에 없는 가설(산화 스트레스, 혈소판 응집, 저도 염증, CoQ10 고갈 등) — 연결 재검토 대상. "
                         "② NIH ODS 영양소 팩트시트는 자동 수집이 차단(403)돼 사용자 브라우저에서 확인함 — 5건 보강(마그네슘→두통, B6→기분 저하, 판토텐산→신경과민, B12·철→체력 저하). "
                         "마그네슘→수면·불안·기분·변비, B군→긴장·불안·스트레스, 철→현기증·혈액순환은 ODS에도 해당 문장이 없음. CoQ10·멜라토닌은 ODS 팩트시트가 없음. "
                         "③ 국내 출처(식약처·질병관리청)는 이번에 검색하지 않음. ④ 비의학 표현(입 텁텁함, 관절 소리 등)."],
    ["기전-성분", "그래프의 식약처 기능성 문구(고시·고시형·개별인정형) 원문을 근거로 연결. 일반 기전은 문구 키워드, 결핍 기전(X*)은 성분 이름으로 자동 매칭 — "
               "키워드 오매칭 가능성이 있어 검토 필요. C3 체액 정체, X9 멜라토닌 고갈, A2 신장 기능 저하는 식약처 기능성 문구로 연결되는 성분이 없음."],
]:
    guide.append(line)
guide.column_dimensions["A"].width = 22
guide.column_dimensions["B"].width = 120
for row in guide.iter_rows():
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")
guide["A1"].font = guide["B1"].font = H

# 대분류 요약
cats = list(dict.fromkeys(r["cat"] for r in rows))
summ = []
for c in cats:
    rs = [r for r in rows if r["cat"] == c]
    p = [r for r in rs if r["level"] == "1차"]
    cnt, pc = collections.Counter(r["status"] for r in rs), collections.Counter(r["status"] for r in p)
    nsym = len({r["symptom"] for r in rs})
    covered = len({r["symptom"] for r in rs if r["status"] == "supported"})
    summ.append([c, nsym, len(rs), cnt["supported"], cnt["partial"], cnt["not_found"],
                 pc["supported"], pc["partial"], pc["not_found"], f"{covered}/{nsym}"])
sheet("대분류 요약", ["대분류", "증상 키워드 수", "연결 수", "근거 있음", "부분 근거", "공식 근거 없음",
                   "1차 근거 있음", "1차 부분", "1차 없음", "근거 있는 기전이 1개 이상인 증상"], summ,
      [14, 12, 9, 10, 10, 13, 12, 10, 10, 22])

# 증상-기전
sheet("증상-기전", ["대분류", "증상", "기전 ID", "기전", "1차/2차", "판정", "출처 기관", "문서 제목", "URL",
                  "인용(원문 그대로)", "인용 번역", "인용 검증", "비고"],
      [[r["cat"], r["symptom"], r["mid"], r["mech"], r["level"], LABEL[r["status"]], r["org"], r["title"], r["url"],
        r["quote"], r["quote_ko"], r["verify"], r["note"]] for r in rows],
      [12, 14, 8, 22, 8, 12, 22, 30, 40, 60, 50, 12, 50], status_col=5)

# 기전-성분
mrows = []
for mid, m in mech.items():
    items = mi_by.get(mid, [])
    if not items:
        mrows.append([mid, m["name"], "", "", "공식 근거 없음", "", "", "", "식약처 기능성 문구로 연결되는 성분 없음"])
    for o in sorted(items, key=lambda o: o["name"]):
        mrows.append([mid, m["name"], o["name"], o["type"], "근거 있음", o["text"], f"식품의약품안전처 ({o['source']})",
                      o["doc_id"], o["rule"]])
sheet("기전-성분", ["기전 ID", "기전", "성분", "성분 유형", "판정", "식약처 기능성 문구(원문)", "출처", "그래프 doc_id",
                   "매칭 규칙(자동, 검토 필요)"], mrows, [8, 22, 26, 10, 12, 60, 28, 26, 40], status_col=4)

# 기전 목록
mlist = []
for mid, m in mech.items():
    rs = [r for r in rows if r["mid"] == mid]
    cnt = collections.Counter(r["status"] for r in rs)
    mlist.append([mid, m["group"], m["name"], m["definition"], len(rs), cnt["supported"], cnt["partial"], cnt["not_found"],
                  len({o["ingredient_id"] for o in mi_by.get(mid, [])})])
sheet("기전 목록", ["기전 ID", "기전 대분류", "기전", "정의(초안)", "증상 연결 수", "근거 있음", "부분 근거", "공식 근거 없음",
                  "연결 성분 수(식약처 문구)"], mlist, [8, 12, 24, 50, 10, 10, 10, 13, 14])

# 출처 목록
sheet("출처 목록", ["source_id", "기관", "문서 제목", "URL", "접근일", "수집 방식", "근거로 쓰인 연결 수"],
      [[sid, s["org"], s["title"], s["url"], s.get("accessed", TODAY), s.get("fetch_method", ""), used[sid]]
       for sid, s in sorted(sources.items()) if used[sid]],
      [26, 30, 50, 60, 12, 12, 14])

for out in OUTS:
    wb.save(out)
    print("saved", out)
print("links", len(rows), dict(tot), "1차", dict(prim))
print("verify fail", sum(1 for r in rows if r["verify"].startswith("실패")))
print("sources used", sum(1 for v in used.values() if v))
for s in summ:
    print(s)
