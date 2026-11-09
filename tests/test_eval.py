import json
from pathlib import Path

from rxrag.contract import STATUSES, validate

PATIENTS = Path(__file__).resolve().parents[1] / "eval" / "patients.json"


def load():
    return json.loads(PATIENTS.read_text(encoding="utf-8"))["patients"]


def test_about_thirty_unique_ids():
    items = load()
    ids = [p["id"] for p in items]
    assert 28 <= len(items) <= 32 and len(ids) == len(set(ids))


def test_every_patient_valid():
    for p in load():
        assert validate(p["patient"])[0] is not None, p["id"]


def test_expect_status_values():
    assert all(p.get("expect_status") in (None, *STATUSES) for p in load())


def test_must_not_computed_for_everyone():
    assert all(isinstance(p.get("must_not"), list) for p in load())


def test_groups_cover_plan():
    groups = {p["group"] for p in load()}
    assert {"픽스처", "archive", "금지", "경계"} <= groups
