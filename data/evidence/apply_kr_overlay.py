"""기존 증상-기전 근거 엑셀에 국내 공식 출처(results/kr_*.json)를 덧붙인다.

국내 우선 규칙: 국내·해외 판정 중 강한 쪽을 최종 판정으로 쓰고, 같으면 국내를 대표 근거로 둔다.
해외(NIH) 근거는 지우지 않고 보조로 남긴다. 인용은 저장한 원문(sources/KR-*.txt)과 코드로 대조한다.

실행: python apply_kr_overlay.py <기존.xlsx> <출력.xlsx> [<출력2.xlsx> ...]
"""
import collections
import json
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

EV = Path(__file__).parent
BASE, OUTS = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
TODAY = "2026-11-09"

LABEL = {"supported": "근거 있음", "partial": "부분 근거", "not_found": "공식 근거 없음"}
RANK = {"근거 있음": 2, "부분 근거": 1, "공식 근거 없음": 0}
FILL = {"근거 있음": "E2F0D9", "부분 근거": "FFF2CC", "공식 근거 없음": "F8CBAD", "미검색": "EDEDED"}
HEAD_FILL = PatternFill("solid", fgColor="D9E1F2")
KR_HEAD_FILL = PatternFill("solid", fgColor="FCE4D6")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


# 1. 국내 결과 읽기 + 인용 대조
sources, kr = {}, {}
for f in sorted((EV / "results").glob("kr_*.json")):
    data = json.load(open(f))
    for s in data["sources"]:
        sources[s["source_id"]] = s
    for r in data["links"]:
        kr[(r["symptom"], r["mech_id"])] = r

texts = {}
for sid, s in sources.items():
    p = EV / s.get("file", f"sources/{sid}.txt")
    texts[sid] = norm(p.read_text(errors="ignore")) if p.exists() else None

# 기존 NIH 판정과 기준 통일: '만성 여부 미명시'만이 감점 사유인 연결은 근거 있음으로 둔다
# (기존 데이터셋은 같은 경우를 근거 있음 + 비고로 처리했다).
CHRONIC_ONLY = {("만성피로", "E1"), ("만성피로", "X1"), ("만성피로", "X2")}
for key in CHRONIC_ONLY:
    if key in kr and kr[key]["status"] == "partial":
        kr[key]["status"] = "supported"
        kr[key]["note"] = "[기존 판정 기준과 통일: 만성 여부 미명시는 근거 있음 + 비고] " + kr[key].get("note", "")

verify_fail = []
for key, r in kr.items():
    r["label"] = LABEL[r["status"]]
    r["check"] = ""
    if r["status"] == "not_found":
        continue
    t = texts.get(r.get("source_id"))
    q = norm(r.get("quote"))
    if t is None:
        r["check"], r["label"] = "원문 파일 없음", "공식 근거 없음"
    elif q and q in t:
        r["check"] = "통과"
    elif q and re.sub(r"\s", "", q) in re.sub(r"\s", "", t):
        r["check"] = "통과(공백 제거 대조: PDF 줄바꿈)"
    else:
        r["check"], r["label"] = "실패(원문에 없음)", "공식 근거 없음"
    if not r["check"].startswith("통과"):
        verify_fail.append(key)

# 2. 증상-기전 시트에 국내 열 추가
wb = openpyxl.load_workbook(BASE)
ws = wb["증상-기전"]
hdr = [c.value for c in ws[1]]
col = {h: i for i, h in enumerate(hdr)}
new_hdr = ["국내 판정", "국내 출처 기관", "국내 문서 제목", "국내 URL", "국내 인용(원문 그대로)", "국내 인용 검증",
           "국내 비고", "최종 판정(국내 우선)", "대표 근거"]
start = len(hdr) + 1
for j, h in enumerate(new_hdr):
    c = ws.cell(row=1, column=start + j, value=h)
    c.font, c.fill = Font(bold=True), KR_HEAD_FILL
for j, w in enumerate([12, 22, 30, 40, 60, 12, 50, 14, 12]):
    ws.column_dimensions[get_column_letter(start + j)].width = w

summary = collections.defaultdict(collections.Counter)
used = collections.Counter()
for row in ws.iter_rows(min_row=2):
    cat, sym, mid, foreign = (row[col[k]].value for k in ("대분류", "증상", "기전 ID", "판정"))
    if not sym:
        continue
    r = kr.get((sym, mid))
    if r is None:
        vals = ["미검색", "", "", "", "", "", "", foreign, "해외(NIH)" if RANK.get(foreign, 0) else "-"]
        final = foreign
    else:
        s = sources.get(r.get("source_id"), {}) if r["label"] != "공식 근거 없음" else {}
        k_rank, f_rank = RANK[r["label"]], RANK.get(foreign, 0)
        final = r["label"] if k_rank >= f_rank else foreign
        rep = "-" if max(k_rank, f_rank) == 0 else ("국내" if k_rank >= f_rank else "해외(NIH, 보조)")
        note = r.get("note", "")
        if (sym, mid) in verify_fail:
            note = f"[인용 검증 {r['check']}] {note}"
        vals = [r["label"], s.get("org", ""), s.get("title", ""), s.get("url", ""),
                r.get("quote", "") if r["label"] != "공식 근거 없음" else "", r["check"], note, final, rep]
        if s:
            used[r["source_id"]] += 1
    for j, v in enumerate(vals):
        c = ws.cell(row=row[0].row, column=start + j, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for j in (0, 7):
        lab = vals[j]
        if lab in FILL:
            ws.cell(row=row[0].row, column=start + j).fill = PatternFill("solid", fgColor=FILL[lab])
    summary[cat]["kr_" + vals[0]] += 1
    summary[cat]["final_" + final] += 1
    summary[cat]["rep_" + vals[8]] += 1
ws.auto_filter.ref = f"A1:{get_column_letter(start + len(new_hdr) - 1)}{ws.max_row}"

# 3. 대분류 요약에 국내·최종 열 추가
sm = wb["대분류 요약"]
sh = [c.value for c in sm[1]]
add = ["국내 근거 있음", "국내 부분", "국내 없음", "국내 미검색", "최종 근거 있음", "최종 부분", "최종 없음", "대표 근거=국내"]
s0 = len(sh) + 1
for j, h in enumerate(add):
    c = sm.cell(row=1, column=s0 + j, value=h)
    c.font, c.fill = Font(bold=True), KR_HEAD_FILL
    sm.column_dimensions[get_column_letter(s0 + j)].width = 12
for row in sm.iter_rows(min_row=2):
    cat = row[0].value
    if not cat or cat not in summary:
        continue
    c = summary[cat]
    for j, v in enumerate([c["kr_근거 있음"], c["kr_부분 근거"], c["kr_공식 근거 없음"], c["kr_미검색"],
                           c["final_근거 있음"], c["final_부분 근거"], c["final_공식 근거 없음"], c["rep_국내"]]):
        sm.cell(row=row[0].row, column=s0 + j, value=v)

# 4. 출처 목록에 국내 출처 추가
so = wb["출처 목록"]
for sid, s in sources.items():
    so.append([sid, s.get("org"), s.get("title"), s.get("url"), s.get("accessed", TODAY),
               s.get("fetch_method"), used.get(sid, 0)])
    for c in so[so.max_row]:
        c.fill = PatternFill("solid", fgColor="FCE4D6")

# 5. 읽는 법에 업데이트 내용 추가
g = wb["읽는 법"]
cats = ", ".join(sorted({k for k in summary if summary[k]["kr_미검색"] < sum(
    v for kk, v in summary[k].items() if kk.startswith("kr_"))}))
tot = collections.Counter()
for k in summary:
    tot.update(summary[k])
g.append(["국내 출처 업데이트", f"{TODAY}. 국내 공식 출처(질병관리청 국가건강정보포털·식약처·보건복지부 KDRI·건보공단)로 재검색. "
          f"대상 대분류: {cats}. 국내 판정: 근거 있음 {tot['kr_근거 있음']}, 부분 {tot['kr_부분 근거']}, "
          f"없음 {tot['kr_공식 근거 없음']}, 미검색 {tot['kr_미검색']}. 인용 검증 실패 {len(verify_fail)}건(없음 처리)."])
g.append(["국내 우선 규칙", "「증상-기전」 시트 주황 머리글 열. 최종 판정 = 국내·해외 판정 중 강한 쪽, 같으면 국내를 대표 근거로 둠. "
          "해외(NIH) 근거는 지우지 않고 보조로 남김. '미검색'은 아직 국내 출처를 찾지 않은 대분류."])
for row in g.iter_rows(min_row=g.max_row - 1):
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")

for out in OUTS:
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print("저장", out)
print("국내 판정", {k: v for k, v in tot.items() if k.startswith("kr_")})
print("최종 판정", {k: v for k, v in tot.items() if k.startswith("final_")})
print("대표 근거", {k: v for k, v in tot.items() if k.startswith("rep_")})
print("인용 검증 실패", verify_fail)
