"""Human labeling tools (P0-10). All sheets here are synthetic."""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import label_tools  # noqa: E402

from mockinterview.bank.loader import DATASET_FILE, load_dataset, load_questions, validate_dataset  # noqa: E402
from mockinterview.evaluate import metrics  # noqa: E402

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


# --- import: comparing labelers ---

def sheet(folder, name, scores, encoding="utf-8-sig"):
    path = folder / f"{name}.csv"
    lines = ["id,question,rubric_summary,answer,score"] + [f'{item},q,r,"an answer",{score}' for item, score in scores.items()]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    return path


def test_compare_reports_pairwise_agreement(tmp_path, dataset):
    paths = [sheet(tmp_path, "ana", {"a01": 3, "a02": 2, "a03": 4}), sheet(tmp_path, "rafi", {"a01": 3, "a02": 1, "a03": 2})]
    report = label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)
    pair = report["pairs"][0]
    assert pair["labelers"] == ["ana", "rafi"] and pair["n"] == 3
    assert pair["within_one"] == pytest.approx(2 / 3) and pair["exact"] == pytest.approx(1 / 3)
    assert pair["qwk"] == pytest.approx(metrics.quadratic_weighted_kappa([3, 2, 4], [3, 1, 2]))


def test_perfect_agreement_has_a_kappa_of_one(tmp_path, dataset):
    scores = {"a01": 1, "a02": 2, "a03": 4}
    paths = [sheet(tmp_path, "ana", scores), sheet(tmp_path, "rafi", scores)]
    report = label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)
    assert report["pairs"][0]["qwk"] == pytest.approx(1.0) and report["disagreements"] == []


def test_every_pair_is_compared(tmp_path, dataset):
    paths = [sheet(tmp_path, name, {"a01": 3}) for name in ("ana", "rafi", "sumi")]
    report = label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)
    assert [p["labelers"] for p in report["pairs"]] == [["ana", "rafi"], ["ana", "sumi"], ["rafi", "sumi"]]


def test_disagreements_are_listed_largest_first(tmp_path, dataset):
    paths = [sheet(tmp_path, "ana", {"a01": 3, "a02": 2, "a03": 4}), sheet(tmp_path, "rafi", {"a01": 2, "a02": 2, "a03": 1})]
    report = label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)
    assert [(d["id"], d["spread"]) for d in report["disagreements"]] == [("a03", 3), ("a01", 1)]
    assert report["disagreements"][0]["scores"] == {"ana": 4, "rafi": 1}


def test_a_blank_score_is_reported_and_left_out_of_the_comparison(tmp_path, dataset):
    paths = [sheet(tmp_path, "ana", {"a01": 3, "a02": 2}), sheet(tmp_path, "rafi", {"a01": 3, "a02": ""})]
    report = label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)
    assert report["pairs"][0]["n"] == 1 and report["missing"] == {"rafi": ["a02"]}


def test_labelers_with_nothing_in_common_do_not_crash(tmp_path, dataset):
    paths = [sheet(tmp_path, "ana", {"a01": 3}), sheet(tmp_path, "rafi", {"a02": 2})]
    assert label_tools.compare_labels(label_tools.read_sheets(paths, dataset), dataset)["pairs"][0] == {
        "labelers": ["ana", "rafi"], "n": 0, "qwk": None, "within_one": None, "exact": None}


def test_excel_style_whole_numbers_are_accepted(tmp_path, dataset):
    paths = [sheet(tmp_path, "ana", {"a01": "3.0"}), sheet(tmp_path, "rafi", {"a01": 3})]
    assert label_tools.read_sheets(paths, dataset)["ana"] == {"a01": 3}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_sheets_saved_by_common_spreadsheet_tools_can_be_read(tmp_path, dataset, encoding):
    path = tmp_path / "ana.csv"
    path.write_bytes(f"id,answer,score\r\na01,café answer,3\r\n".encode(encoding))
    assert label_tools.read_sheets([path, sheet(tmp_path, "rafi", {"a01": 3})], dataset)["ana"] == {"a01": 3}


@pytest.mark.parametrize("bad", ["5", "0", "2.5", "good"])
def test_invalid_scores_name_the_sheet_and_answer(tmp_path, dataset, bad):
    paths = [sheet(tmp_path, "ana", {"a01": bad}), sheet(tmp_path, "rafi", {"a01": 3})]
    with pytest.raises(label_tools.SheetError, match=r"ana\.csv, a01"):
        label_tools.read_sheets(paths, dataset)


def test_unusable_sheets_are_rejected(tmp_path, dataset):
    good = sheet(tmp_path, "rafi", {"a01": 3})
    with pytest.raises(label_tools.SheetError, match="at least 2"):
        label_tools.read_sheets([good], dataset)
    with pytest.raises(label_tools.SheetError, match="unknown answer id"):
        label_tools.read_sheets([sheet(tmp_path, "ana", {"zzz": 3}), good], dataset)
    with pytest.raises(label_tools.SheetError, match="rename the file"):
        label_tools.read_sheets([sheet(tmp_path, "my labels", {"a01": 3}), good], dataset)
    duplicate = tmp_path / "dup.csv"
    duplicate.write_text("id,score\na01,3\na01,2\n", encoding="utf-8")
    with pytest.raises(label_tools.SheetError, match="appears twice"):
        label_tools.read_sheets([duplicate, good], dataset)
    no_scores = tmp_path / "bare.csv"
    no_scores.write_text("id,answer\na01,text\n", encoding="utf-8")
    with pytest.raises(label_tools.SheetError, match="'id' and 'score'"):
        label_tools.read_sheets([no_scores, good], dataset)


def test_import_command_prints_kappa_and_saves_a_report(tmp_path, dataset, capsys):
    paths = [sheet(tmp_path, "ana", {"a01": 3, "a02": 1}), sheet(tmp_path, "rafi", {"a01": 2, "a02": 1})]
    report = tmp_path / "report.json"
    assert label_tools.main(["import", str(paths[0]), str(paths[1]), "--dataset", str(dataset), "--out", str(report)]) == 0
    output = capsys.readouterr().out
    assert "ana vs rafi: n=2" in output and "QWK" in output and "a01 [tech_hashmap]: ana=3  rafi=2" in output
    assert json.loads(report.read_text(encoding="utf-8"))["pairs"][0]["n"] == 2


def test_a_bad_sheet_is_a_clear_error_not_a_traceback(tmp_path, dataset, capsys):
    good = sheet(tmp_path, "rafi", {"a01": 3})
    assert label_tools.main(["import", str(good), "--dataset", str(dataset)]) == 2
    assert "at least 2" in capsys.readouterr().err


# --- adjudicate ---

def labels(dataset, *sheets_scores, resolutions=None, **kwargs):
    """Adjudicate pre-parsed sheets named l0, l1, ..."""
    return label_tools.adjudicate({f"l{i}": scores for i, scores in enumerate(sheets_scores)}, dataset, resolutions, **kwargs)


def item(dataset, answer_id):
    return next(a for a in load_dataset(dataset) if a["id"] == answer_id)


def test_agreement_becomes_a_reviewed_score_and_keeps_the_draft(dataset):
    report = labels(dataset, {"a01": 2}, {"a01": 2})
    answer = item(dataset, "a01")
    assert (answer["score"], answer["label_status"], answer["draft_score"], answer["labelers"]) == (2, "reviewed", 3, ["l0", "l1"])
    assert report["reviewed"] == ["a01"] and report["changed_from_draft"] == ["a01"] and report["written"]


def test_agreeing_with_the_draft_is_not_counted_as_a_change(dataset):
    report = labels(dataset, {"a01": 3}, {"a01": 3})
    assert report["changed_from_draft"] == [] and item(dataset, "a01")["draft_score"] == 3


def test_a_disagreement_is_never_averaged_or_guessed(dataset):
    before = dataset.read_bytes()
    report = labels(dataset, {"a01": 2}, {"a01": 3})
    assert report["unresolved"] == [{"id": "a01", "scores": {"l0": 2, "l1": 3}}] and report["reviewed"] == [] and not report["written"]
    assert dataset.read_bytes() == before


def test_a_recorded_resolution_settles_a_disagreement(dataset):
    labels(dataset, {"a01": 2}, {"a01": 4}, resolutions={"a01": 3})
    answer = item(dataset, "a01")
    assert answer["score"] == 3 and answer["label_status"] == "reviewed"


def test_a_resolution_cannot_override_agreement(dataset):
    before = dataset.read_bytes()
    with pytest.raises(label_tools.SheetError, match="not in dispute: a01"):
        labels(dataset, {"a01": 2}, {"a01": 2}, resolutions={"a01": 4})
    assert dataset.read_bytes() == before


def test_a_majority_of_three_is_consensus_but_a_three_way_split_is_not(dataset):
    report = labels(dataset, {"a01": 2, "a02": 1}, {"a01": 2, "a02": 2}, {"a01": 3, "a02": 3})
    assert report["reviewed"] == ["a01"] and item(dataset, "a01")["score"] == 2 and [u["id"] for u in report["unresolved"]] == ["a02"]


def test_one_label_is_not_enough_for_review(dataset):
    report = labels(dataset, {"a01": 3}, {"a02": 2})
    assert report["too_few_labels"] == ["a01", "a02"] and item(dataset, "a01")["label_status"] == "draft"


def test_readjudicating_keeps_the_original_draft_score(dataset):
    labels(dataset, {"a01": 2}, {"a01": 2})
    labels(dataset, {"a01": 4}, {"a01": 4})
    answer = item(dataset, "a01")
    assert answer["score"] == 4 and answer["draft_score"] == 3 and answer["label_status"] == "reviewed"


def test_an_item_reviewed_earlier_keeps_its_draft_score(dataset):
    labels(dataset, {"r01": 4}, {"r01": 4})
    answer = item(dataset, "r01")
    assert answer["score"] == 4 and answer["draft_score"] == 3 and answer["labelers"] == ["l0", "l1"]


def test_dry_run_writes_nothing(dataset):
    before = dataset.read_bytes()
    report = labels(dataset, {"a01": 2}, {"a01": 2}, dry_run=True)
    assert report["reviewed"] == ["a01"] and not report["written"] and dataset.read_bytes() == before


def test_comments_answers_and_other_items_are_untouched(dataset):
    before = dataset.read_text(encoding="utf-8")
    labels(dataset, {"a01": 2}, {"a01": 2})
    after = dataset.read_text(encoding="utf-8")
    assert "# Header comment that must survive." in after and "# Section comment that must survive." in after
    assert "score: 1 looks like a key but is answer text." in after
    assert after.split("- id: a02", 1)[1] == before.split("- id: a02", 1)[1]


def test_crlf_line_endings_are_preserved(tmp_path):
    path = tmp_path / "crlf.yaml"
    path.write_bytes(DATASET.replace("\n", "\r\n").encode("utf-8"))
    labels(path, {"a01": 2}, {"a01": 2})
    raw = path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") and item(path, "a01")["score"] == 2


def test_labeler_names_that_look_like_yaml_values_stay_strings(tmp_path, dataset):
    paths = [sheet(tmp_path, "no", {"a01": 2}), sheet(tmp_path, "true", {"a01": 2})]
    label_tools.adjudicate(label_tools.read_sheets(paths, dataset), dataset)
    assert item(dataset, "a01")["labelers"] == ["no", "true"]


def test_a_malformed_item_writes_nothing(tmp_path):
    path = tmp_path / "bad.yaml"
    text = DATASET.replace("    question_id: tech_hashmap\n    score: 3\n", "    score: 3\n", 1).replace('    score: 3\n    label_status: draft\n    reason: "Secret draft reason one."\n', '    "score": 3\n    label_status: draft\n    reason: "Secret draft reason one."\n', 1)
    path.write_bytes(text.encode("utf-8"))
    before = path.read_bytes()
    with pytest.raises(label_tools.SheetError):
        labels(path, {"a01": 2}, {"a01": 2})
    assert path.read_bytes() == before


def test_the_real_dataset_changes_only_on_the_label_lines(tmp_path):
    path = tmp_path / "dataset.yaml"
    path.write_bytes(DATASET_FILE.read_bytes())
    answers = load_dataset(path)
    agreed = {a["id"]: 1 if a["score"] == 4 else a["score"] + 1 for a in answers}
    before = path.read_text(encoding="utf-8").splitlines()
    report = labels(path, agreed, agreed)
    after = path.read_text(encoding="utf-8").splitlines()
    assert len(report["reviewed"]) == len(answers) == len(report["changed_from_draft"])
    removed, added = [line for line in before if line not in after], [line for line in after if line not in before]
    assert {line.strip().split(":")[0] for line in removed} <= {"score", "label_status"}
    assert {line.strip().split(":")[0] for line in added} <= {"score", "draft_score", "label_status", "labelers"}
    assert [a["answer"] for a in load_dataset(path)] == [a["answer"] for a in answers]
    assert validate_dataset(load_dataset(path), load_questions()) == []


def test_round_trip_from_export_to_reviewed_labels(tmp_path, dataset, capsys):
    blank = tmp_path / "sheet.csv"
    label_tools.export_sheet(blank, dataset)
    rows = read_rows(blank)

    def fill(name, scores):
        path = tmp_path / f"{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=label_tools.SHEET_COLUMNS)
            writer.writeheader()
            writer.writerows({**row, "score": scores[row["id"]]} for row in rows)
        return str(path)

    ana, rafi = fill("ana", {"a01": 4, "a02": 1, "a03": 3}), fill("rafi", {"a01": 4, "a02": 2, "a03": 3})
    assert label_tools.main(["import", ana, rafi, "--dataset", str(dataset)]) == 0
    assert "ana vs rafi: n=3" in capsys.readouterr().out
    assert label_tools.main(["adjudicate", ana, rafi, "--dataset", str(dataset)]) == 0
    assert "unresolved a02: ana=1  rafi=2" in capsys.readouterr().out
    assert item(dataset, "a02")["label_status"] == "draft"

    settled = tmp_path / "resolved.csv"
    settled.write_text("id,score\na02,2\n", encoding="utf-8")
    assert label_tools.main(["adjudicate", ana, rafi, "--resolved", str(settled), "--dataset", str(dataset)]) == 0
    capsys.readouterr()
    reviewed = {a["id"]: a for a in load_dataset(dataset)}
    assert (reviewed["a01"]["score"], reviewed["a01"]["draft_score"]) == (4, 3)
    assert (reviewed["a02"]["score"], reviewed["a02"]["label_status"]) == (2, "reviewed")
    assert reviewed["a03"]["labelers"] == ["ana", "rafi"]
    label_tools.export_sheet(blank, dataset)
    assert read_rows(blank) == []
