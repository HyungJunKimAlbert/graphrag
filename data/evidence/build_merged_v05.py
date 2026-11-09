"""v0.4 매핑(근거 먼저·국내) + 기존 근거 데이터셋(판정·인용 검증·식약처 문구 매칭)을 합친 v0.5 엑셀을 만든다.

원칙
- v0.4의 행(근거 170, 기전 56, 기능성 태그 연결)은 하나도 빼지 않는다. 기전 이름은 v0.4가 기준이다.
- 기존 데이터셋에서는 장점만 가져온다: 국내 파일럿 근거(근거 있음·부분), NIH '근거 있음'(해외 보조),
  식약처 기능성 문구 기반 기전→성분 연결. 근거 없음·NIH 부분 근거는 「제외 목록」에 남긴다.
- 모든 인용은 저장한 원문(sources/)과 코드로 대조한다.

실행: python build_merged_v05.py <v0.4.xlsx> <out.xlsx> [<out2.xlsx> ...]
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
V04, OUTS = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
OLD = Path("/Users/user/Documents/projects/wellcheck/data/symptom_mechanism_evidence.xlsx")
MT = Path("/Users/user/Documents/projects/wellcheck/data/mechanism_table.xlsx")
ING = Path("/Users/user/Documents/documents/웰체크협업/건기식/건기식원료_DB_261030_API정리.xlsx")
TODAY = "2026-11-09"

RANK = {"근거 있음": 2, "부분 근거": 1}
FILL = {"근거 있음": "E2F0D9", "부분 근거": "FFF2CC", "공식 근거 없음": "F8CBAD"}
PARTIAL_PAT = re.compile(r"간접|근사|해석|대응|다른 문장|다른 문단|가설|아니|없어|없다|추정")
# 기존 기전 이름 → v0.4 기전 이름 (이름이 같은 18개는 자동 대응)
ALIAS = {
    "HPA축 과활성(스트레스 호르몬)": "HPA축 과활성",
    "철 결핍": "미량영양소 결핍(철)",
    "비타민B12·엽산 부족": "미량영양소 결핍(비타민B12)",
    "마그네슘 부족": "미량영양소 결핍(마그네슘)",
    "칼륨 부족": "미량영양소 결핍(칼륨)",
    "칼슘·비타민D 부족": "미량영양소 결핍(비타민D)",
    "근육량·근력 저하": "근육량 감소",
    "여성호르몬 변화(갱년기, 월경)": "여성호르몬 변화",
    "남성호르몬·전립선 변화": "남성호르몬(안드로겐) 작용",
    "말초 신경 압박·손상": "말초신경 손상",
}
APPROX = {"비타민B12·엽산 부족", "칼슘·비타민D 부족", "남성호르몬·전립선 변화"}  # 범위가 완전히 같지는 않음
# kns.or.kr 게시판 페이지 대신 보건복지부 KDRI 원문 PDF로 대조한 인용
KDRI = {"vitamins": "KR-MOHW-kdri2020-vitamins", "minerals": "KR-MOHW-kdri2020-minerals"}
# 겹치지 않아 유지하는 추가 기전: v0.4 이름 규칙에 맞춘 이름, 영양제 대상이 아닌 기전은 진료 권고
RENAME = {"비타민B군 부족": "미량영양소 결핍(비타민B6·판토텐산)", "아연·미네랄 부족": "미량영양소 결핍(아연)",
          "간 해독 부담": "간 기능 저하(간질환)"}
TREAT_OVERRIDE = {"간 기능 저하(간질환)": "진료 권고", "약물 직접 부작용": "진료 권고", "신장 기능 저하": "진료 권고"}
# v0.4 기전과 뜻이 겹치는 추가 기전 → 제외(v0.4 유지)
OVERLAP_MECH = {"눈 표면·시각 기능 저하": "눈물막 불안정·미량영양소 결핍(비타민A)",
                "두피·모발 성장 저하": "남성호르몬(안드로겐) 작용"}
KO_FIX = {"Loss of hair": "아연 결핍 증상: 탈모"}  # 번역이 너무 짧은 해외 인용 보완
CHRONIC_ONLY = {("만성피로", "E1"), ("만성피로", "X1"), ("만성피로", "X2")}


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def nows(s):
    return re.sub(r"\s", "", str(s or "")).replace("･", "·").replace("ㆍ", "·")


def rows(ws, start=2):
    return [r for r in ws.iter_rows(min_row=start, values_only=True)]


src_text = {}


def text_of(sid, file=None):
    if sid not in src_text:
        p = EV / (file or f"sources/{sid}.txt")
        src_text[sid] = p.read_text(errors="ignore") if p.exists() else None
    return src_text[sid]


def verify(quote, sid, file=None):
    t = text_of(sid, file)
    if t is None:
        return "원문 파일 없음"
    if norm(quote) in norm(t):
        return "통과"
    if nows(quote) in nows(t):
        return "통과(공백 제거 대조)"
    return "실패"


# ---------- 1. v0.4 읽기 ----------
w4 = openpyxl.load_workbook(V04, data_only=True)
cat_def, sym_order = {}, []  # sym_order: (대분류, 증상) — 같은 증상이 두 대분류에 있을 수 있음
PILOT_CAT = "피로/활력"
for r in rows(w4["증상-기전"]):
    if r[2]:
        cat_def[r[0]] = r[1]
        sym_order.append((r[0], r[2]))
v_mech = [r for r in rows(w4["기전 목록"]) if r[1]]
v_tag = [r for r in rows(w4["기전-기능성 태그"]) if r[1]]
tag_content = {r[0]: r[1] for r in rows(w4["기능성 태그(통합)"]) if r[0]}
v04_idx = {i["url"]: i for i in json.load(open(EV / "v04_sources.json"))}

mechs = collections.OrderedDict()  # 이름 → 정보
for i, r in enumerate(v_mech, 1):
    mechs[r[1]] = {"id": f"M{i:02d}", "group": r[0], "name": r[1], "tags": r[3] or "", "origin": "v0.4",
                   "old_ids": [], "treat": set()}
for r in v_tag:
    mechs[r[1]]["treat"].add(r[5])

# 기존 기전 ID ↔ 이름
mt = openpyxl.load_workbook(MT, read_only=True, data_only=True)
old_mech = {r[0]: {"group": r[1], "name": r[2]} for r in rows(mt["기전 체계"]) if r[0]}
old_to_v = {}
for oid, m in old_mech.items():
    vname = ALIAS.get(m["name"], m["name"])
    if vname in mechs:
        old_to_v[oid] = vname
        mechs[vname]["old_ids"].append(oid + ("(근사)" if m["name"] in APPROX else ""))


def mech_for_old(oid):
    """기존 ID를 v0.5 기전 이름으로. v0.4에 없으면 기존 기전을 새로 추가한다."""
    if oid in old_to_v:
        return old_to_v[oid]
    m = old_mech[oid]
    name = RENAME.get(m["name"], m["name"])
    if name not in mechs:
        mechs[name] = {"id": "", "group": m["group"], "name": name, "tags": "",
                       "origin": "기존 데이터셋에서 추가", "old_ids": [oid], "treat": set()}
    old_to_v[oid] = name
    return name


# ---------- 2. 근거 행 ----------
ev_rows, sources = [], {}

for r in rows(w4["기전 근거"]):
    if not r[1]:
        continue
    cat, sym, grp, mname, expl, quote, org_title, url = r[:8]
    s = v04_idx[url]
    sid, file = s["source_id"], s["file"]
    check = verify(quote, sid, file)
    note = ""
    if check == "실패" and "kns.or.kr" in url:  # 게시판 페이지 → KDRI 원문 PDF
        for k in ("vitamins", "minerals"):
            c2 = verify(quote, KDRI[k])
            if c2.startswith("통과"):
                check, note = f"{c2}: KDRI 원문 PDF", f"인용 대조는 보건복지부 2020 KDRI {'비타민' if k == 'vitamins' else '무기질'}편 PDF({KDRI[k]})"
                sources[KDRI[k]] = {"org": "보건복지부·한국영양학회", "title": f"2020 한국인 영양소 섭취기준 - {'비타민' if k == 'vitamins' else '무기질'}",
                                    "url": f"https://www.mohw.go.kr/boardDownload.es?bid=0019&list_no=362385&seq={2 if k == 'vitamins' else 3}",
                                    "method": "curl(pdftotext)", "kind": "국내"}
                break
    org, _, title = str(org_title).partition(" · ")
    sources.setdefault(sid, {"org": org, "title": title, "url": url, "method": s["fetch_method"], "kind": "국내"})
    verdict = "부분 근거" if PARTIAL_PAT.search(str(expl or "")) else "근거 있음"
    if not check.startswith("통과"):
        verdict, note = "공식 근거 없음", f"[인용 검증 실패] {note}"
    ev_rows.append(dict(cat=cat, sym=sym, mech=mname, verdict=verdict, expl=expl or "", quote=quote, org=org,
                        title=title, url=url, kind="국내", check=check, file=file, origin="v0.4", note=note,
                        rule="v0.4 근거 설명의 표현(간접·근사·해석 등)으로 판정"))

# 국내 파일럿(피로/활력)
excluded = []
url_sid = {i["url"]: i["source_id"] for i in v04_idx.values()}  # 같은 페이지는 v0.4 출처 ID 하나로
kr = json.load(open(EV / "results" / "kr_fatigue.json"))
kr_src = {s["source_id"]: s for s in kr["sources"]}
for l in kr["links"]:
    key = (l["symptom"], l["mech_id"])
    mname_old = old_mech[l["mech_id"]]["name"]
    if l["status"] == "not_found":
        excluded.append([PILOT_CAT, l["symptom"], l["mech_id"], mname_old, "국내 파일럿: 근거 못 찾음",
                         l.get("note", "")])
        continue
    status = "supported" if key in CHRONIC_ONLY else l["status"]
    mname = mech_for_old(l["mech_id"])
    qk = nows(l["quote"])
    dup = [e for e in ev_rows if e["cat"] == PILOT_CAT and e["sym"] == l["symptom"] and e["mech"] == mname
           and (qk in nows(e["quote"]) or nows(e["quote"]) in qk)]
    if dup:  # 같은 문장(한쪽이 다른 쪽을 포함) → v0.4 행을 남기고 파일럿 판정이 더 강하면 올린다
        pv = {"supported": "근거 있음", "partial": "부분 근거"}[status]
        for e in dup:
            e["note"] = (e["note"] + " [국내 파일럿과 같은 인용]").strip()
            if RANK[pv] > RANK.get(e["verdict"], 0):
                e["verdict"], e["note"] = pv, e["note"] + f" [파일럿 판정({pv})으로 상향]"
        continue
    s = kr_src[l["source_id"]]
    check = verify(l["quote"], l["source_id"], s.get("file"))
    s = {**s, "source_id": url_sid.get(s["url"], l["source_id"])}  # 같은 URL이면 v0.4 출처 ID로 통일
    sources.setdefault(s["source_id"], {"org": s["org"], "title": s["title"], "url": s["url"],
                                        "method": s.get("fetch_method"), "kind": "국내"})
    note = l.get("note", "")
    if key in CHRONIC_ONLY:
        note = "[기존 판정 기준과 통일: 만성 여부 미명시는 근거 있음] " + note
    ev_rows.append(dict(cat=PILOT_CAT, sym=l["symptom"], mech=mname,
                        verdict={"supported": "근거 있음", "partial": "부분 근거"}[status] if check.startswith("통과") else "공식 근거 없음",
                        expl="", quote=l["quote"], org=s["org"], title=s["title"], url=s["url"], kind="국내",
                        check=check, file=s.get("file"), origin="국내 파일럿(2026-11-09)", note=note,
                        rule="같은 문장에서 증상·기전 연결=근거 있음, 넓은 범주·다른 문단=부분"))

# 기존 NIH 근거: '근거 있음'만 해외 보조로
wo = openpyxl.load_workbook(OLD, data_only=True)
ws = wo["증상-기전"]
h = {c.value: i for i, c in enumerate(ws[1])}
for r in rows(ws):
    if not r[1]:
        continue
    cat, sym, oid, oname, verdict = r[h["대분류"]], r[h["증상"]], r[h["기전 ID"]], r[h["기전"]], r[h["판정"]]
    if verdict != "근거 있음":
        excluded.append([cat, sym, oid, oname, f"기존 NIH 판정: {verdict}", r[h["비고"]] or ""])
        continue
    mname = mech_for_old(oid)
    ev_rows.append(dict(cat=cat, sym=sym, mech=mname, verdict="근거 있음", expl=r[h["인용 번역"]] or "",
                        quote=r[h["인용(원문 그대로)"]], org=r[h["출처 기관"]], title=r[h["문서 제목"]], url=r[h["URL"]],
                        kind="해외(보조)", check=r[h["인용 검증"]], file="", origin="기존 NIH 근거", note=r[h["비고"]] or "",
                        rule="기존 데이터셋 판정"))
old_src = {r[3]: r for r in rows(wo["출처 목록"]) if r[0]}
for e in ev_rows:
    if e["kind"] == "해외(보조)" and e["url"] in old_src:
        o = old_src[e["url"]]
        sources.setdefault(o[0], {"org": o[1], "title": o[2], "url": o[3], "method": o[5], "kind": "해외(보조)"})

# v0.4와 겹치면 v0.4를 남기고 추가분을 뺀다: ① 뜻이 겹치는 추가 기전 ② v0.4에 이미 있는 증상-기전 연결에 덧붙인 근거
v04_pairs = {(e["cat"], e["sym"], e["mech"]) for e in ev_rows if e["origin"] == "v0.4"}
kept = []
for e in ev_rows:
    if e["origin"] != "v0.4":
        reason = None
        if e["mech"] in OVERLAP_MECH:
            reason = f"v0.4 기전과 겹침({OVERLAP_MECH[e['mech']]}) → v0.4 유지"
        elif (e["cat"], e["sym"], e["mech"]) in v04_pairs:
            reason = "v0.4에 같은 증상-기전 연결 있음 → v0.4 근거만 유지"
        if reason:
            excluded.append([e["cat"], e["sym"], ", ".join(mechs[e["mech"]]["old_ids"]) or "", e["mech"],
                             f"{e['origin']}·{e['verdict']}: {reason}", e["quote"]])
            continue
    if e["kind"] == "해외(보조)" and e["quote"] in KO_FIX:
        e["expl"] = KO_FIX[e["quote"]]
    kept.append(e)
ev_rows = kept
alive = {e["mech"] for e in ev_rows}
for name in [n for n, m in mechs.items() if m["origin"] != "v0.4" and n not in alive]:
    del mechs[name]
    for oid in [o for o, v in old_to_v.items() if v == name]:
        del old_to_v[oid]
for i, m in enumerate(mechs.values(), 1):
    m["id"] = f"M{i:02d}"
    if m["name"] in TREAT_OVERRIDE:
        m["treat"] = {TREAT_OVERRIDE[m["name"]]}

for e in ev_rows:
    e["mid"] = mechs[e["mech"]]["id"]
    e["group"] = mechs[e["mech"]]["group"]

# ---------- 3. 기전 → 성분 ----------
wi = openpyxl.load_workbook(ING, data_only=True)
ingredients = []  # (이름, 유형, 기능성 문구, 출처)
for r in rows(wi["2-1. 영양성분"], 3):
    if r[1] and r[4]:
        ingredients.append((str(r[1]).strip(), "영양성분", norm(r[4]), "식약처 고시 제2025-11호"))
for r in rows(wi["2-2. 고시형"]):
    if r[1] and r[4]:
        ingredients.append((str(r[1]).strip(), "고시형", norm(r[4]), "식약처 고시 제2025-11호"))
for r in rows(wi["2-3. 개별인정형(API)"]):
    if r[5] and r[7]:
        func = re.sub(r"\(영문\).*", "", norm(r[7]), flags=re.S).replace("(국문)", "").strip()
        ingredients.append((str(r[5]).strip(), "개별인정형", func, f"식약처 개별인정 {r[0]} (I-0050)"))
ing_by_name = {}
for i in ingredients:
    ing_by_name.setdefault(i[0], i)


def resolve(name, fallback):
    """그래프 이름(인정번호 없음)을 엑셀 원료 DB 이름으로 맞춘다.
    정확히 같은 이름이 있으면 그것, 없으면 '이름(…제XXXX-XX호)'로 시작하는 개별인정 원료 전부, 그래도 없으면 공백 무시 일치."""
    if name in ing_by_name:
        return [ing_by_name[name]]
    pre = [i for n, i in ing_by_name.items() if n.startswith(name + "(") and re.search(r"제\d{4}-\d+호\)$", n)]
    if pre:
        return pre
    same = [i for n, i in ing_by_name.items() if nows(n) == nows(name)]
    return same or [fallback]

mi_rows = collections.OrderedDict()


def add_mi(mname, ing, path):
    key = (mname, ing[0])
    if key in mi_rows:
        if path not in mi_rows[key]["paths"]:
            mi_rows[key]["paths"].append(path)
        return
    mi_rows[key] = {"mech": mname, "ing": ing, "paths": [path]}


for r in v_tag:
    mname, tag, nut = r[1], r[2], r[4]
    if nut:  # 결핍 기전: v0.4가 지정한 영양성분
        for n in re.split(r",\s*", nut):
            for ing in resolve(n.strip(), (n.strip(), "영양성분", "", "")):
                add_mi(mname, ing, "v0.4 지정 영양성분")
    if tag and tag in tag_content:
        phrases = [p.strip() for p in re.split(r",\s*", tag_content[tag]) if len(nows(p)) >= 3]
        for ing in ingredients:
            # '눈의 피로(도) 개선'은 눈 기능성이므로 눈이 아닌 구절('피로 개선')과 맞추지 않는다
            body = nows(ing[2])
            body_wo_eye = re.sub(r"눈의피로도?개선", "", body)
            hit = [p for p in phrases if nows(p) in (body if "눈" in p else body_wo_eye)]
            if hit:
                add_mi(mname, ing, f"태그 '{tag}' 구절 일치: {hit[0]}")
for o in json.load(open(EV / "mech_ingredient.json")):
    if o["mech_id"] not in old_to_v:  # 근거 행이 없는 기존 기전(예: CoQ10 고갈)은 추가하지 않는다
        continue
    mname = old_to_v[o["mech_id"]]
    gtype = {"individual": "개별인정형(그래프에만 있음)", "group": "영양성분 묶음(그래프)"}.get(o["type"], o["type"])
    for ing in resolve(o["name"], (o["name"], gtype, norm(o["text"]), o["source"])):
        add_mi(mname, ing, f"기존 기전 {o['mech_id']} 자동 매칭({o['rule']})")

# ---------- 4. 집계 ----------
pairs = collections.OrderedDict()
for e in ev_rows:
    if e["verdict"] == "공식 근거 없음":
        continue
    p = pairs.setdefault((e["cat"], e["sym"], e["mech"]), {"rows": []})
    p["rows"].append(e)
for (cat, sym, mname), p in pairs.items():
    best = max(RANK[e["verdict"]] for e in p["rows"])
    p["final"] = {2: "근거 있음", 1: "부분 근거"}[best]
    dom = [e for e in p["rows"] if e["kind"] == "국내" and RANK[e["verdict"]] == best]
    p["rep"] = "국내" if dom else "해외(보조)"
    p["n_dom"] = sum(1 for e in p["rows"] if e["kind"] == "국내")
    p["n_for"] = len(p["rows"]) - p["n_dom"]
for key in [k for k in mi_rows if k[0] in TREAT_OVERRIDE]:  # 진료 권고 기전에는 성분을 붙이지 않는다(v0.4와 같은 규칙)
    del mi_rows[key]
mi_count = collections.Counter(k[0] for k in mi_rows)
for m in mechs.values():
    if not m["treat"]:
        m["treat"] = {"기능성(기존 매칭)"} if mi_count[m["name"]] else {"연결 없음"}

# ---------- 5. 엑셀 쓰기 ----------
wb = openpyxl.Workbook()
wb.remove(wb.active)
HEAD = PatternFill("solid", fgColor="D9E1F2")


def sheet(title, header, data, widths, verdict_cols=()):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for d in data:
        ws.append(d)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for vc in verdict_cols:
            v = row[vc].value
            if v in FILL:
                row[vc].fill = PatternFill("solid", fgColor=FILL[v])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


n_ev = collections.Counter((e["origin"], e["verdict"]) for e in ev_rows)
n_pair = collections.Counter(p["final"] for p in pairs.values())
n_rep = collections.Counter(p["rep"] for p in pairs.values())
new_mechs = [m["name"] for m in mechs.values() if m["origin"] != "v0.4"]
guide = [
    ["이 파일", f"증상 → 기전 → 성분 매핑 v0.5. 작성 {TODAY}. v0.4(근거 먼저·국내 출처)를 뼈대로, 기존 근거 데이터셋"
               "(symptom_mechanism_evidence.xlsx)의 장점을 합침. 약사 검토 전."],
    ["v0.4에서 가져온 것", "근거 170행·기전 56개·기전-기능성 태그·처리 구분(진료 권고/금기 참고/연결 없음)을 전부 유지. 빠진 행 없음."],
    ["기존 데이터셋에서 가져온 것", "① 판정 열(근거 있음/부분 근거) ② 인용 원문 코드 대조 ③ 국내 파일럿(피로/활력) 근거 "
                         "④ NIH '근거 있음'(해외 보조) ⑤ 식약처 기능성 문구 기반 기전→성분 연결."],
    ["뺀 것", f"「제외 목록」({len(excluded)}행)에 사유와 함께 남김. ① 기존 데이터셋의 '공식 근거 없음'·NIH '부분 근거' "
            "② v0.4와 겹치는 추가분: v0.4에 이미 있는 증상-기전 연결에 덧붙인 근거(v0.4 근거만 유지), "
            "v0.4 기전과 뜻이 겹치는 추가 기전(눈 표면·시각 기능 저하→눈물막 불안정·비타민A 결핍, 두피·모발 성장 저하→남성호르몬 작용) "
            "③ CoQ10 고갈(국내·해외 모두 근거 없음)."],
    ["v0.4 우선 규칙", "v0.4와 겹치면 v0.4를 남기고 추가분을 뺀다. 그래서 v0.4에 있는 증상-기전 연결은 판정·대표 근거가 v0.4 근거만으로 정해지고, "
                   "추가 근거(국내 파일럿·NIH)는 v0.4에 없는 연결에만 쓰인다."],
    ["추가 기전 정리", "겹치지 않아 유지한 추가 기전은 v0.4 이름 규칙에 맞춤: 비타민B군 부족→미량영양소 결핍(비타민B6·판토텐산), "
                  "아연·미네랄 부족→미량영양소 결핍(아연), 간 해독 부담→간 기능 저하(간질환). "
                  "간 기능 저하(간질환)·약물 직접 부작용·신장 기능 저하는 처리 구분 '진료 권고'(성분 연결 없음)."],
    ["근거 행", f"총 {len(ev_rows)}행. " + ", ".join(f"{o}·{vv} {v}" for (o, vv), v in sorted(n_ev.items()))],
    ["중복 처리", "① 근거: 같은 대분류·증상·기전에서 인용이 같거나 한쪽이 다른 쪽을 포함하면 v0.4 행 하나만 남김(파일럿 판정이 더 강하면 상향). "
              "② 출처: 같은 URL은 출처 ID 하나로 통일. ③ 성분: 그래프 이름(인정번호 없음)을 원료 DB 이름(인정번호 포함)으로 맞춘 뒤 (기전, 성분) 단위로 합침. "
              "고시형 '인삼'과 개별인정 '인삼(제2021-10호)'처럼 인정이 다른 원료는 별개로 둠. "
              "같은 증상이 두 대분류에 있는 경우(소화불량, 부종)는 대분류별로 따로 셈."],
    ["증상-기전 연결", f"{len(pairs)}쌍. 근거 있음 {n_pair['근거 있음']}, 부분 근거 {n_pair['부분 근거']}. "
                  f"대표 근거 국내 {n_rep['국내']}, 해외(보조)만 {n_rep['해외(보조)']}."],
    ["판정 기준", "근거 있음: 한 출처 안에서 증상과 기전(원인·결핍·질환)을 잇는 문장. 부분 근거: 가설 수준, 다른 문단, 증상 범위가 다름, "
              "기전 이름에 대응시킨 해석. v0.4 행은 v0.4 '근거' 설명에 간접·근사·해석·가설 등의 표현이 있으면 부분 근거로 자동 분류(검토 필요)."],
    ["국내 우선", "쌍의 최종 판정은 근거 행 중 가장 강한 판정. 같은 강도의 국내 근거가 있으면 대표 근거=국내. "
              "해외(NIH)는 국내 근거가 더 약하거나 없을 때만 대표가 됨."],
    ["인용 검증", "인용을 저장한 원문(data/evidence/sources/)과 대조. v0.4 170행 전부 통과(한국영양학회 게시판 인용 4건은 보건복지부 KDRI 원문 PDF로 대조). "
              "실패하면 '공식 근거 없음'으로 처리."],
    ["기전 ID", f"M01~M{len(mechs):02d}는 이 파일에서 새로 붙인 ID(공식 코드 아님). v0.4 기전 56개 + 기존 데이터셋에서 추가한 기전 {len(new_mechs)}개"
              f"({', '.join(new_mechs)}). 「기전 목록」의 '기존 기전 ID'로 이전 ID(E1, X1 …)와 대응. (근사)는 범위가 완전히 같지 않음."],
    ["기전-성분", "경로 3가지: v0.4 지정 영양성분(결핍 기전), v0.4 기능성 태그의 기능성 구절이 식약처 기능성 문구에 들어 있음, "
              "기존 기전 ID 자동 매칭. 모두 자동 매칭이라 검토 필요. 처리 구분이 '진료 권고'·'금기 참고'인 기전은 성분 추천 대상이 아님."],
]
gw = wb.create_sheet("읽는 법")
gw.append(["항목", "설명"])
for g in guide:
    gw.append(g)
gw.column_dimensions["A"].width, gw.column_dimensions["B"].width = 22, 120
for row in gw.iter_rows():
    for c in row:
        c.alignment = Alignment(wrap_text=True, vertical="top")
for c in gw[1]:
    c.font, c.fill = Font(bold=True), HEAD

# 대분류 요약
summ = []
for cat in cat_def:
    syms = [s for c, s in sym_order if c == cat]
    ps = [(k, p) for k, p in pairs.items() if k[0] == cat]
    per_sym = collections.Counter(k[1] for k, _ in ps)
    summ.append([cat, len(syms), len(ps), sum(p["final"] == "근거 있음" for _, p in ps),
                 sum(p["final"] == "부분 근거" for _, p in ps), sum(p["rep"] == "국내" for _, p in ps),
                 sum(p["rep"] == "해외(보조)" for _, p in ps), sum(1 for s in syms if per_sym[s] <= 1),
                 sum(1 for s in syms if per_sym[s] == 0)])
sheet("대분류 요약", ["대분류", "증상 수", "증상-기전 연결", "근거 있음", "부분 근거", "대표 근거=국내", "대표 근거=해외(보조)",
                   "기전 1개 이하 증상", "기전 0개 증상"], summ, [14, 8, 12, 10, 10, 12, 14, 14, 12])

# 증상-기전(v0.4 모양, 확장)
sm = []
for c, s in sym_order:
    ps = [(k[2], p) for k, p in pairs.items() if k[0] == c and k[1] == s]
    ps.sort(key=lambda x: (-RANK[x[1]["final"]], x[1]["rep"] != "국내", mechs[x[0]]["id"]))
    sm.append([c, cat_def[c], s,
               "\n".join(f"{mechs[m]['id']} {m} [{p['final']}·{p['rep']}]" for m, p in ps), len(ps),
               sum(p["rep"] == "국내" for _, p in ps)])
sheet("증상-기전", ["분류명", "한 줄 정의", "증상 키워드", "기전 [최종 판정·대표 근거]", "기전 수", "국내 대표 기전 수"], sm,
      [12, 40, 16, 70, 8, 10])

# 증상-기전 판정(긴 형식: 그래프 적재용)
pl = []
for (c, s, m), p in pairs.items():
    rep = sorted(p["rows"], key=lambda e: (-RANK[e["verdict"]], e["kind"] != "국내"))[0]
    pl.append([c, s, mechs[m]["id"], mechs[m]["group"], m, p["final"], p["rep"], p["n_dom"], p["n_for"],
               ", ".join(sorted(mechs[m]["treat"])), rep["org"], rep["title"], rep["url"],
               rep["expl"] if rep["kind"] == "해외(보조)" else rep["quote"],  # 해외 원문은 기존 데이터셋의 한글 번역
               rep["quote"] if rep["kind"] == "해외(보조)" else ""])
pl.sort(key=lambda x: (sym_order.index((x[0], x[1])), -RANK[x[5]], x[2]))
sheet("증상-기전 판정", ["분류명", "증상 키워드", "기전 ID", "기전 분류", "기전", "최종 판정", "대표 근거", "국내 근거 행 수",
                     "해외 근거 행 수", "처리 구분", "대표 출처 기관", "대표 문서", "대표 URL",
                     "대표 인용(한글: 국내는 원문, 해외는 번역)", "대표 인용 영문 원문(해외만)"], pl,
      [12, 16, 8, 14, 26, 11, 11, 9, 9, 14, 22, 26, 40, 70, 50], verdict_cols=(5,))

# 기전 근거(행 단위)
er = sorted(ev_rows, key=lambda e: (sym_order.index((e["cat"], e["sym"])), e["mid"],
                                    e["kind"] != "국내"))
# 해외 행: 근거(원문) 칸에는 기존 데이터셋의 한글 번역, 영문 원문은 맨 끝 칸에 따로 남긴다(인용 검증·추적용)
FOREIGN = "해외(보조)"
sheet("기전 근거", ["분류명", "증상 키워드", "기전 분류", "기전 ID", "기전", "판정", "근거(설명)",
                  "근거(한글: 국내는 원문, 해외는 번역)", "출처 기관", "문서", "URL", "출처 구분", "인용 검증", "원문 파일",
                  "데이터 원천", "판정 방식", "비고", "근거 영문 원문(해외만)"],
      [[e["cat"], e["sym"], e["group"], e["mid"], e["mech"], e["verdict"],
        "" if e["kind"] == FOREIGN else e["expl"],
        e["expl"] if e["kind"] == FOREIGN else e["quote"], e["org"], e["title"],
        e["url"], e["kind"], e["check"], e["file"], e["origin"], e["rule"], e["note"],
        e["quote"] if e["kind"] == FOREIGN else ""] for e in er],
      [12, 14, 12, 8, 24, 11, 40, 60, 20, 22, 36, 10, 12, 24, 16, 24, 40, 50], verdict_cols=(5,))

# 기전 목록
sym_by_mech = collections.defaultdict(list)
for (c, s, m) in pairs:
    if s not in sym_by_mech[m]:
        sym_by_mech[m].append(s)
sheet("기전 목록", ["기전 ID", "기전 분류", "기전", "증상 키워드", "기능성 태그", "처리 구분", "연결 성분 수", "기존 기전 ID", "원천"],
      [[m["id"], m["group"], m["name"], ", ".join(sym_by_mech[m["name"]]), m["tags"], ", ".join(sorted(m["treat"])),
        mi_count[m["name"]], ", ".join(m["old_ids"]), m["origin"]] for m in mechs.values()],
      [8, 14, 28, 50, 30, 16, 10, 14, 18])

# 기전-기능성 태그(v0.4 그대로 + ID)
sheet("기전-기능성 태그", ["기전 ID", "기전 분류", "기전", "기능성 태그", "태그 기능성 내용", "영양성분", "처리 구분"],
      [[mechs[r[1]]["id"], *r[:6]] for r in v_tag], [8, 14, 28, 20, 40, 16, 14])

# 기전-성분
sheet("기전-성분", ["기전 ID", "기전", "성분", "성분 유형", "식약처 기능성 문구(원문)", "출처", "매칭 경로(자동, 검토 필요)"],
      [[mechs[v["mech"]]["id"], v["mech"], v["ing"][0], v["ing"][1], v["ing"][2], v["ing"][3], "\n".join(v["paths"])]
       for v in sorted(mi_rows.values(), key=lambda v: (mechs[v["mech"]]["id"], v["ing"][1], v["ing"][0]))],
      [8, 26, 30, 10, 60, 24, 50])

# 기능성 태그(통합) v0.4 그대로
sheet("기능성 태그(통합)", ["통합 태그", "포함 기능성 내용"], [[k, v] for k, v in tag_content.items()], [20, 90])

# 출처 목록
used = collections.Counter()
url_to_sid = {s["url"]: sid for sid, s in sources.items()}
for e in ev_rows:
    sid = url_to_sid.get(e["url"])
    if sid:
        used[sid] += 1
for e in ev_rows:
    m = re.search(r"\((KR-MOHW-kdri2020-\w+)\)", e["note"])
    if m:
        used[m.group(1)] += 1
sheet("출처 목록", ["source_id", "구분", "기관", "문서 제목", "URL", "수집 방식", "근거로 쓰인 행 수"],
      [[sid, s["kind"], s["org"], s["title"], s["url"], s["method"], used[sid]]
       for sid, s in sorted(((k, v) for k, v in sources.items() if used[k]), key=lambda x: (x[1]["kind"] != "국내", x[1]["org"] or "", x[0]))],
      [26, 10, 24, 36, 50, 14, 10])

# 제외 목록
sheet("제외 목록", ["분류명", "증상", "기존 기전 ID", "기존 기전", "제외 사유", "비고"], excluded, [12, 16, 10, 24, 22, 70])

for out in OUTS:
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print("저장", out)
print("근거 행", len(ev_rows), dict(n_ev))
print("쌍", len(pairs), dict(n_pair), dict(n_rep))
print("기전", len(mechs), "추가", new_mechs)
print("기전-성분", len(mi_rows), "제외", len(excluded))
print("검증 실패", [(e["sym"], e["mech"], e["origin"]) for e in ev_rows if not str(e["check"]).startswith("통과")])
