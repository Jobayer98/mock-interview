"""Human labeling of the eval dataset: export blind sheets, compare labelers, write consensus scores.

    python evals/label_tools.py export

Sheets never contain draft scores or reasons, so labelers stay independent of the Claude-written drafts (I10).
Filled sheets can hold real candidates' answers, so they stay out of git.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.bank.loader import DATASET_FILE, load_dataset, load_questions  # noqa: E402

SHEET_COLUMNS = ["id", "question", "rubric_summary", "answer", "score"]
DEFAULT_SHEET = ROOT / "evals" / "label_sheet.csv"
_FORMULA_STARTS = ("=", "+", "-", "@")


def rubric_summary(question: dict) -> str:
    return " | ".join(f"{level}: {text}" for level, text in sorted(question["rubric"].items()))


def _safe_cell(text: str) -> str:
    """Answers are untrusted, and a spreadsheet would run a cell that starts like a formula (I3)."""
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def export_sheet(out: Path, dataset: Path = DATASET_FILE) -> int:
    """Write one blind sheet of every answer still awaiting human review. Returns the number of rows."""
    questions = {q["id"]: q for q in load_questions()}
    rows = []
    for answer in load_dataset(dataset):
        if answer["label_status"] != "draft":
            continue
        question = questions[answer["question_id"]]
        rows.append({"id": answer["id"], "question": _safe_cell(question["question"]), "rubric_summary": _safe_cell(rubric_summary(question)),
                     "answer": _safe_cell(" ".join(answer["answer"].split())), "score": ""})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as sheet:  # the byte-order mark lets Excel read it as UTF-8
        writer = csv.DictWriter(sheet, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _cmd_export(args: argparse.Namespace) -> int:
    count = export_sheet(args.out, args.dataset)
    print(f"wrote {count} answers to {args.out}. Give each labeler their own copy, named after them, and score 1-4 in the last column.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Human labeling of the eval dataset")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write a blind sheet to copy for each labeler")
    export.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    export.add_argument("--dataset", type=Path, default=DATASET_FILE)
    export.set_defaults(run=_cmd_export)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
