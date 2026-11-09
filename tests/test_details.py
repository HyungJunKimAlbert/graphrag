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
    out = details.attach(FakeDB(ROWS), recs, raw={"NTC-2-54": "L-글루타민, 에틸아민"})
    assert out[0]["details"] == {"daily_intake": "L-테아닌으로서 200 ~ 250 mg.", "raw_materials": "L-글루타민, 에틸아민",
                                 "precautions": "(가) 임산부 및 수유부는 섭취를 피할 것",
                                 "function": "스트레스로 인한 긴장완화에 도움을 줄 수 있음"}
    assert out[1]["details"]["raw_materials"] is None and out[1]["details"]["precautions"] is None
    assert recs[0].get("details") is None  # 입력은 바꾸지 않는다


def test_attach_one_query_and_empty_list():
    db = FakeDB(ROWS)
    details.attach(db, [{"ingredient_id": "NTC-2-54"}, {"ingredient_id": "NUT-45669"}], raw={})
    assert len(db.calls) == 1 and sorted(db.calls[0]["ids"]) == ["NTC-2-54", "NUT-45669"]
    assert details.attach(db, [], raw={}) == [] and len(db.calls) == 1


def test_attach_missing_ingredient_gives_empty_details():
    out = details.attach(FakeDB([]), [{"ingredient_id": "GRP-vitamin-b"}], raw={})
    assert out[0]["details"] == {"daily_intake": None, "raw_materials": None, "precautions": None, "function": None}


def test_raw_materials_file_loads():
    raw = details.load_raw()
    assert len(raw) > 400 and raw["NTC-2-54"].startswith("L-글루타민")
