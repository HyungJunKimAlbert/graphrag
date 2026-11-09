"""추천 성분 세부사항(일일섭취량·원재료명·섭취 시 주의사항·주된 기능성). LLM 선택 뒤 코드가 붙인다."""
from rxrag import details


class FakeDB:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def run(self, query, **params):
        self.calls.append(params)
        return [r for r in self.rows if r[0] in params["ids"]]


ROWS = [("NTC-2-54", "L-테아닌으로서 200 ~ 250 mg.", "(가) 임산부 및 수유부는 섭취를 피할 것", "스트레스로 인한 긴장완화에 도움을 줄 수 있음"),
        ("NUT-45669", "0.72 ~ 2,000 μg.", None, "1. 정상적인 엽산 대사에 필요")]


def test_attach_adds_details_from_graph_and_raw_materials():
    recs = [{"rank": 1, "ingredient_id": "NTC-2-54", "name": "테아닌"}, {"rank": 2, "ingredient_id": "NUT-45669", "name": "비타민 B12"}]
    out = details.attach(FakeDB(ROWS), recs, raw={"NTC-2-54": "L-글루타민, 에틸아민"}, official={})
    assert out[0]["details"] == {"daily_intake": "L-테아닌으로서 200 ~ 250 mg.", "raw_materials": "L-글루타민, 에틸아민",
                                 "precautions": "(가) 임산부 및 수유부는 섭취를 피할 것",
                                 "function": "스트레스로 인한 긴장완화에 도움을 줄 수 있음"}
    assert out[1]["details"]["raw_materials"] is None and out[1]["details"]["precautions"] is None
    assert recs[0].get("details") is None  # 입력은 바꾸지 않는다


def test_attach_one_query_and_empty_list():
    db = FakeDB(ROWS)
    details.attach(db, [{"ingredient_id": "NTC-2-54"}, {"ingredient_id": "NUT-45669"}], raw={}, official={})
    assert len(db.calls) == 1 and sorted(db.calls[0]["ids"]) == ["NTC-2-54", "NUT-45669"]
    assert details.attach(db, [], raw={}, official={}) == [] and len(db.calls) == 1


def test_attach_missing_ingredient_gives_empty_details():
    out = details.attach(FakeDB([]), [{"ingredient_id": "GRP-vitamin-b"}], raw={}, official={})
    assert out[0]["details"] == {"daily_intake": None, "raw_materials": None, "precautions": None, "function": None}


def test_raw_materials_file_loads():
    raw = details.load_raw()
    assert len(raw) > 400 and raw["NTC-2-54"].startswith("L-글루타민")


OFFICIAL = {"NUT-45669": {"raw_materials": {"value": "비타민 B12(Cyanocobalamin), 식품원료를 사용하여 비타민 B12를 보충할 수 있도록 제조･가공한 것",
                                            "ref": "식약처 고시 제3. 1-12 1)(1)"},
                          "precautions": {"value": "- 덮어쓰면 안 되는 문구", "ref": "테스트"}}}


def test_official_fills_only_empty_fields():
    # 그래프·엑셀 값이 비었을 때만 공식 출처 값으로 채운다. 있는 값은 덮어쓰지 않는다
    rows = [("NUT-45669", "0.72 ~ 2,000 μg.", "- 이상사례 발생 시 섭취를 중단하고 전문가와 상담할 것", "1. 정상적인 엽산 대사에 필요")]
    out = details.attach(FakeDB(rows), [{"ingredient_id": "NUT-45669"}], raw={}, official=OFFICIAL)
    d = out[0]["details"]
    assert d["raw_materials"].startswith("비타민 B12(Cyanocobalamin)")
    assert d["precautions"] == "- 이상사례 발생 시 섭취를 중단하고 전문가와 상담할 것"


def test_official_file_has_reference_for_every_value():
    official = details.load_official()
    assert {"NUT-45669", "NUT-45673", "NUT-45674", "NUT-45675", "NUT-45677", "IND-2025-45", "NUT-45667"} <= set(official)
    for iid, fields in official.items():
        assert set(fields) <= {"daily_intake", "raw_materials", "precautions", "function"}, iid
        for field, entry in fields.items():
            assert entry["value"].strip() and entry["ref"].strip(), (iid, field)
