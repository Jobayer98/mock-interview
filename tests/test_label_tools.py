"""Human labeling tools (P0-10). All sheets here are synthetic."""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import label_tools  # noqa: E402

from mockinterview.bank.loader import DATASET_FILE, load_dataset, load_questions, validate_dataset  # noqa: E402

DATASET = """# Header comment that must survive.
answers:

  # Section comment that must survive.
  - id: a01
    question_id: tech_hashmap
    score: 3
    label_status: draft
    reason: "Secret draft reason one."
    answer: >
      A dictionary gives O(1) lookup on average.
      score: 1 looks like a key but is answer text.

  - id: a02
    question_id: tech_hashmap
    score: 2
    label_status: draft
    tags: [adversarial]
    reason: "Secret draft reason two."
    answer: >
      =HYPERLINK("http://example.com") is my answer.

  - id: a03
    question_id: tech_sql_joins
    score: 4
    label_status: draft
    reason: "Secret draft reason three."
    answer: >
      An inner join returns matching rows.

  - id: r01
    question_id: tech_sql_joins
    score: 2
    label_status: reviewed
    draft_score: 3
    labelers: ["earlier", "people"]
    reason: "Already reviewed."
    answer: >
      A left join keeps every left row.
"""


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "dataset.yaml"
    path.write_bytes(DATASET.encode("utf-8"))
    return path


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as sheet:
        return list(csv.DictReader(sheet))


# --- export ---

def test_export_has_exactly_the_blind_columns_and_blank_scores(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    assert label_tools.export_sheet(out, dataset) == 3
    rows = read_rows(out)
    assert list(rows[0]) == ["id", "question", "rubric_summary", "answer", "score"]
    assert all(row["score"] == "" for row in rows)


def test_export_leaks_no_draft_scores_reasons_or_tags(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    label_tools.export_sheet(out, dataset)
    raw = out.read_text(encoding="utf-8-sig")
    for leaked in ("Secret draft reason", "label_status", "adversarial", "draft"):
        assert leaked not in raw


def test_export_of_the_real_dataset_contains_no_reason(tmp_path):
    out = tmp_path / "sheet.csv"
    answers = load_dataset(DATASET_FILE)
    assert label_tools.export_sheet(out, DATASET_FILE) == sum(a["label_status"] == "draft" for a in answers)
    raw = out.read_text(encoding="utf-8-sig")
    anchors = {q["id"]: set(q["rubric"].values()) for q in load_questions()}  # a draft reason may simply repeat an anchor, which every row shows
    assert not [a["id"] for a in answers if a["reason"] in raw and a["reason"] not in anchors[a["question_id"]]]


def test_export_skips_answers_already_reviewed(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    label_tools.export_sheet(out, dataset)
    assert [row["id"] for row in read_rows(out)] == ["a01", "a02", "a03"]


def test_export_includes_the_rubric_anchors(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    label_tools.export_sheet(out, dataset)
    question = next(q for q in load_questions() if q["id"] == "tech_hashmap")
    assert read_rows(out)[0]["rubric_summary"].startswith(f"1: {question['rubric'][1]} | 2: ")


def test_export_answer_is_one_line(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    label_tools.export_sheet(out, dataset)
    assert read_rows(out)[0]["answer"] == "A dictionary gives O(1) lookup on average. score: 1 looks like a key but is answer text."


def test_an_answer_cannot_run_as_a_spreadsheet_formula(tmp_path, dataset):
    out = tmp_path / "sheet.csv"
    label_tools.export_sheet(out, dataset)
    assert read_rows(out)[1]["answer"].startswith("'=HYPERLINK")


def test_export_command_reports_where_it_wrote(tmp_path, dataset, capsys):
    out = tmp_path / "nested" / "sheet.csv"
    assert label_tools.main(["export", "--out", str(out), "--dataset", str(dataset)]) == 0
    assert out.exists() and "wrote 3 answers" in capsys.readouterr().out
