"""추천 결과를 누적 통계 파일에 쌓고 대시보드 HTML을 만든다.

    python3 dashboard/build_stats.py                 # out/recommendations.json → out/reco_stats/*.csv, out/reco_dashboard.html
    python3 dashboard/build_stats.py --src <파일>... --label "점수제 v2"
    python3 dashboard/build_stats.py --reset         # 누적 파일을 비우고 다시 쌓는다
    python3 dashboard/build_stats.py --open          # 만든 뒤 브라우저로 연다

같은 결과(환자 + 내용이 같은 건)는 다시 넣어도 한 번만 센다. 로직을 바꿔 결과가 달라지면 새 건으로 쌓인다.
그래프·LLM을 부르지 않는다.

누적 파일은 CSV 여섯 개다 (out/reco_stats/). 같은 글자를 반복해 적지 않도록 표를 나눴다.
    results.csv      결과 1건 = 1행 (사용자, 주호소, 정규화 증상, 진료 판정)
    candidates.csv   결과 × 후보 성분 = 1행 (단계, 순위, 경로·주의·금지 번호)
    paths.csv        경로 사전 (증상→기전→기능성 등)
    ingredients.csv  성분 사전 (원료 번호, 이름, 유형)
    notes.csv        주의·금지 문구 사전
    runs.csv         실행 기록
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STATS = OUT / "reco_stats"            # CSV 묶음
HTML = OUT / "reco_dashboard.html"
TEMPLATE = pathlib.Path(__file__).with_name("template.html")
CATEGORIES = pathlib.Path(__file__).with_name("symptom_categories.json")   # 증상 → 대분류
SEP, IDS = " | ", ";"                  # 칸 안의 목록 구분: 글자 목록은 " | ", 번호 목록은 ";"
RESULT_COLS = ["key", "run", "patient_id", "age", "sex", "chief", "outcome", "care_level", "care_reason", "care_message",
               "symptom_phrases", "symptom_names", "unmatched", "held", "held_removed", "life_stage"]
TABLES = {"results": RESULT_COLS, "candidates": ["result", "ingredient_id", "tier", "rank", "paths", "cautions", "blocks"],
          "paths": ["path_id", "type", "p1", "p2", "p3"], "ingredients": ["ingredient_id", "name", "kind"],
          "notes": ["note_id", "text"], "runs": ["id", "label", "source", "source_mtime", "ingested_at", "records"]}
KIND = {"NUT": "영양성분", "NTC": "고시형", "IND": "개별인정형"}
NO_FUNCTION = "드럭머거"     # 기능성 없이 영양소 결핍으로 끝나는 경로 (pipeline._export 와 같은 표기)


def symptom_strands(why):
    out = []
    for path in filter(None, (why or "").split(" / ")):
        parts = path.split(" → ")
        if len(parts) == 2:
            parts.append(NO_FUNCTION)
        if len(parts) == 3:
            out.append(["S", *parts])
    return out


def drug_strand(why):
    drug, _, effect = why.partition(" → ")
    return ["D", drug.removesuffix(" 복용").strip(), effect.strip() or "영양소 고갈", NO_FUNCTION]


def life_strands(life, stage):
    """'증상 ← 질환·질환 / ...' → 생애주기 → 질환 → 증상 가닥."""
    out = []
    for part in filter(None, (life or "").split(" / ")):
        symptom, _, diseases = part.partition(" ← ")
        out += [["L", stage, d.strip(), symptom.strip()] for d in diseases.split("·") if d.strip()]
    return out


def interest_strands(why):
    _, _, names = why.partition("관심사 ")
    return [["I", n.strip()] for n in names.split(", ") if n.strip()]


def compact(r):
    """결과 한 건에서 대시보드가 쓰는 값만 남긴다."""
    step1, funnel = r["step1"], r.get("funnel")
    rec = {"patient_id": r["patient_id"], "age": r["age"], "sex": r["sex"], "chief": r["chief_complaint"],
           "outcome": r["outcome"], "care_level": step1["care_level"], "care_reason": step1["care_reason"],
           "care_message": r.get("message") or "",
           "symptoms": [{"phrase": s["phrase"], "name": s["name"]} for s in step1["symptoms"]],
           "unmatched": step1.get("unmatched", []), "held": step1.get("held_symptoms", []),
           "held_removed": step1.get("held_ingredients", 0),
           "life_stage": (r.get("step2") or {}).get("life_stage"), "items": []}
    if not funnel:
        return rec
    strands = {}
    for s in funnel["symptom"]:
        strands.setdefault(s["ingredient_id"], []).extend(symptom_strands(s["why"]))
        strands[s["ingredient_id"]].extend(life_strands(s.get("life"), rec["life_stage"] or "생애주기 미상"))
    for d in funnel["drug"]:
        strands.setdefault(d["ingredient_id"], []).append(drug_strand(d["why"]))
    for i in funnel["interest"]:
        strands.setdefault(i["ingredient_id"], []).extend(interest_strands(i["why"]))
    caution, block = {}, {}
    for s in funnel["safety"]:
        if s["action"] == "제외":       # 금지 문구 + 사용자 쪽에서 걸린 것(나이·질환·약)
            block.setdefault(s["ingredient_id"], []).extend(
                f'{b["text"]} (해당: {b["user"] or b["match"]})' for b in s["basis"] if b.get("severity") == "avoid")
        else:
            caution.setdefault(s["ingredient_id"], []).extend(b["text"] for b in s["basis"])
    rank = {c["ingredient_id"]: c["rank"] for c in r.get("top10", [])}
    picked = {c["ingredient_id"] for c in r.get("picks", [])}
    for c in funnel["kept"]:
        k = c["ingredient_id"]
        uniq = [list(t) for t in dict.fromkeys(tuple(s) for s in strands.get(k, []))]
        rec["items"].append({"id": k, "name": c["name"], "kind": KIND.get(k.split("-")[0], "기타"),
                             "rank": rank.get(k), "tier": 3 if k in picked else 2 if k in rank else 1,
                             "caution": list(dict.fromkeys(caution.get(k, []))), "block": [], "strands": uniq})
    # 안전 검증에서 금지로 빠진 후보 (tier 0). 추천 건수에는 들어가지 않고, 대시보드의 '금지 제외' 범위에서만 본다
    dropped = funnel.get("dropped") if isinstance(funnel.get("dropped"), list) else []
    for d in dropped:
        k = d["ingredient_id"]
        if k not in block:
            continue
        uniq = [list(t) for t in dict.fromkeys(tuple(s) for s in strands.get(k, []))]
        rec["items"].append({"id": k, "name": d["name"], "kind": KIND.get(k.split("-")[0], "기타"), "rank": None, "tier": 0, "caution": [],
                             "block": list(dict.fromkeys(block[k])), "strands": uniq})
    return rec


def to_tables(stats):
    """누적 결과 → 표 여섯 개(행 목록). 경로·성분·문구는 사전으로 빼고 후보에는 번호만 적는다."""
    paths, notes, ings = {}, {}, {}
    t = {name: [] for name in TABLES}
    for n, r in enumerate(stats["records"]):
        t["results"].append([r["key"], r["run"], r["patient_id"], r["age"], r["sex"], r["chief"], r["outcome"], r["care_level"],
                             r["care_reason"], r["care_message"], SEP.join(s["phrase"] for s in r["symptoms"]),
                             SEP.join(s["name"] for s in r["symptoms"]), SEP.join(r["unmatched"]), SEP.join(r["held"]),
                             r["held_removed"], r["life_stage"] or ""])
        for it in r["items"]:
            ings.setdefault(it["id"], [it["id"], it["name"], it["kind"]])
            num = lambda texts: IDS.join(str(notes.setdefault(x, len(notes))) for x in texts)
            t["candidates"].append([n, it["id"], it["tier"], it["rank"] or "",
                                    IDS.join(str(paths.setdefault(tuple(s), len(paths))) for s in it["strands"]),
                                    num(it["caution"]), num(it["block"])])
    t["paths"] = [[i, *p, *[""] * (4 - len(p))] for p, i in paths.items()]
    t["ingredients"] = list(ings.values())
    t["notes"] = [[i, x] for x, i in notes.items()]
    t["runs"] = [[u[c] for c in TABLES["runs"]] for u in stats["runs"]]
    return t


def to_csv(name, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TABLES[name])
    w.writerows(rows)
    return buf.getvalue()


def load():
    """CSV 묶음 → 누적 결과. 열 구성이 다르면(예전 형식) 다시 쌓으라고 알린다."""
    t = {}
    for name, cols in TABLES.items():
        with (STATS / f"{name}.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        if rows[0] != cols:
            raise SystemExit(f"{STATS.name}/{name}.csv 형식이 예전 것입니다. 원본 결과 파일로 --reset 해서 다시 쌓으세요.")
        t[name] = [dict(zip(cols, r)) for r in rows[1:]]
    cut = lambda s, sep=SEP: s.split(sep) if s else []
    paths = {p["path_id"]: [v for v in (p["type"], p["p1"], p["p2"], p["p3"]) if v != ""] for p in t["paths"]}
    ings = {i["ingredient_id"]: i for i in t["ingredients"]}
    notes = {n["note_id"]: n["text"] for n in t["notes"]}
    records = []
    for r in t["results"]:
        names = cut(r["symptom_names"])
        records.append({**{k: r[k] for k in ("key", "run", "patient_id", "sex", "chief", "outcome", "care_level", "care_reason", "care_message")},
                        "age": int(r["age"]), "symptoms": [{"phrase": p, "name": n} for p, n in zip(cut(r["symptom_phrases"]), names)],
                        "unmatched": cut(r["unmatched"]), "held": cut(r["held"]), "held_removed": int(r["held_removed"] or 0),
                        "life_stage": r["life_stage"] or None, "items": []})
    for c in t["candidates"]:
        g = ings[c["ingredient_id"]]
        records[int(c["result"])]["items"].append(
            {"id": g["ingredient_id"], "name": g["name"], "kind": g["kind"], "rank": int(c["rank"]) if c["rank"] else None,
             "tier": int(c["tier"]), "caution": [notes[i] for i in cut(c["cautions"], IDS)],
             "block": [notes[i] for i in cut(c["blocks"], IDS)], "strands": [paths[i] for i in cut(c["paths"], IDS)]})
    return {"runs": [{**u, "records": int(u["records"])} for u in t["runs"]], "records": records}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", nargs="*", default=[str(OUT / "recommendations.json")])
    ap.add_argument("--label", help="이번에 넣는 결과의 이름 (기본: 원본 파일 수정 시각)")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    stats = {"runs": [], "records": []}
    if (STATS / "results.csv").exists() and not args.reset:
        stats = load()
    seen = {r["key"] for r in stats["records"]}
    now = datetime.datetime.now()
    for src in map(pathlib.Path, args.src):
        mtime = datetime.datetime.fromtimestamp(src.stat().st_mtime)
        run_id = mtime.strftime("%y%m%d-%H%M")
        added = skipped = 0
        for r in json.loads(src.read_text(encoding="utf-8")):
            rec = compact(r)
            key = r["patient_id"] + ":" + hashlib.sha1(      # 원본 결과가 같으면 같은 건
                json.dumps(r, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            stats["records"].append({"key": key, "run": run_id, **rec})
            added += 1
        if added:
            run = next((x for x in stats["runs"] if x["id"] == run_id), None)
            if run:
                run["records"] += added
            else:
                stats["runs"].append({"id": run_id, "label": args.label or run_id, "source": src.name,
                                      "source_mtime": mtime.isoformat(timespec="seconds"),
                                      "ingested_at": now.isoformat(timespec="seconds"), "records": added})
        print(f"{src.name}: 새로 넣음 {added}건, 이미 있음 {skipped}건")
    STATS.mkdir(parents=True, exist_ok=True)
    texts = {name: to_csv(name, rows) for name, rows in to_tables(stats).items()}
    for name, text in texts.items():
        (STATS / f"{name}.csv").write_text(text, encoding="utf-8")
    cats = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    page = {"csv": texts, "categories": cats["symptoms"], "updated_at": now.isoformat(timespec="seconds")}     # 화면도 같은 CSV 를 읽는다
    data = json.dumps(page, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    HTML.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data), encoding="utf-8")
    items = sum(len(r["items"]) for r in stats["records"])
    print(f"누적 {len(stats['records'])}건 (실행 {len(stats['runs'])}회, 후보 성분 {items}개) → {STATS}\n대시보드 → {HTML}")
    if args.open:
        subprocess.run(["open", str(HTML)], check=False)


if __name__ == "__main__":
    main()
