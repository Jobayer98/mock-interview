"""Human labeling of the eval dataset: export blind sheets, compare labelers, write consensus scores.

    python evals/label_tools.py export
    python evals/label_tools.py import evals/labels/alice.csv evals/labels/bob.csv

Sheets never contain draft scores or reasons, so labelers stay independent of the Claude-written drafts (I10).
Filled sheets can hold real candidates' answers, so they stay out of git.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.bank.loader import DATASET_FILE, SCORES, load_dataset, load_questions  # noqa: E402
from mockinterview.evaluate import metrics  # noqa: E402

SHEET_COLUMNS = ["id", "question", "rubric_summary", "answer", "score"]
DEFAULT_SHEET = ROOT / "evals" / "label_sheet.csv"
MIN_LABELERS = 2
LABELER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_FORMULA_STARTS = ("=", "+", "-", "@")


class SheetError(ValueError):
    """A labeling sheet cannot be used as it stands."""


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


def _read_text(path: Path) -> str:
    """Excel's "CSV UTF-8" and Google Sheets give UTF-8; Excel's plain "CSV" on Windows gives cp1252."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _parse_score(raw: str, source: str, item: str) -> int:
    try:
        value = float(raw)
    except ValueError:
        raise SheetError(f"{source}, {item}: score {raw!r} is not a number from 1 to 4") from None
    if value not in SCORES:
        raise SheetError(f"{source}, {item}: score {raw!r} is not a whole number from 1 to 4")
    return int(value)


def _read_scores(path: Path, known_ids: set[str]) -> dict[str, int]:
    """Scores by answer id. A blank score means the item was not labeled."""
    reader = csv.DictReader(io.StringIO(_read_text(path), newline=""))
    if not {"id", "score"} <= set(reader.fieldnames or []):
        raise SheetError(f"{path.name}: needs 'id' and 'score' columns")
    scores: dict[str, int] = {}
    seen: set[str] = set()
    for row in reader:
        item, raw = (row["id"] or "").strip(), (row["score"] or "").strip()
        if item not in known_ids:
            raise SheetError(f"{path.name}: unknown answer id {item!r}")
        if item in seen:
            raise SheetError(f"{path.name}: answer id {item!r} appears twice")
        seen.add(item)
        if raw:
            scores[item] = _parse_score(raw, path.name, item)
    return scores


def read_sheets(paths: list[Path], dataset: Path = DATASET_FILE) -> dict[str, dict[str, int]]:
    """Scores by labeler, who is named by the file: alice.csv is 'alice'."""
    if len(paths) < MIN_LABELERS:
        raise SheetError(f"need sheets from at least {MIN_LABELERS} labelers, got {len(paths)}")
    known_ids = {a["id"] for a in load_dataset(dataset)}
    sheets: dict[str, dict[str, int]] = {}
    for path in paths:
        if not LABELER_NAME.match(path.stem):
            raise SheetError(f"{path.name}: rename the file to the labeler's name, using only letters, digits, _ or -")
        if path.stem in sheets:
            raise SheetError(f"two sheets are named {path.stem!r}")
        sheets[path.stem] = _read_scores(path, known_ids)
    return sheets


def compare_labels(sheets: dict[str, dict[str, int]], dataset: Path = DATASET_FILE) -> dict:
    """How far the humans agree with each other. This is the ceiling the grader can be expected to reach."""
    pairs = []
    for first, second in combinations(sorted(sheets), 2):
        shared = sorted(set(sheets[first]) & set(sheets[second]))
        a, b = [sheets[first][i] for i in shared], [sheets[second][i] for i in shared]
        pairs.append({"labelers": [first, second], "n": len(shared),
                      "qwk": metrics.quadratic_weighted_kappa(a, b) if shared else None,
                      "within_one": metrics.within_one(a, b) if shared else None,
                      "exact": metrics.exact_match(a, b) if shared else None})
    disagreements = []
    for answer in load_dataset(dataset):
        scores = _scores_for(answer["id"], sheets)
        if len(set(scores.values())) > 1:
            disagreements.append({"id": answer["id"], "question_id": answer["question_id"], "scores": scores,
                                  "spread": max(scores.values()) - min(scores.values())})
    disagreements.sort(key=lambda d: -d["spread"])
    labeled = set().union(*sheets.values())
    return {"labelers": sorted(sheets), "items_labeled": len(labeled), "pairs": pairs, "disagreements": disagreements,
            "missing": {name: sorted(labeled - set(sheet)) for name, sheet in sorted(sheets.items()) if labeled - set(sheet)}}


def _scores_for(item: str, sheets: dict[str, dict[str, int]]) -> dict[str, int]:
    return {name: sheet[item] for name, sheet in sorted(sheets.items()) if item in sheet}


def _cmd_import(args: argparse.Namespace) -> int:
    report = compare_labels(read_sheets(args.sheets, args.dataset), args.dataset)
    print(f"labelers: {', '.join(report['labelers'])} | answers labeled: {report['items_labeled']}")
    for pair in report["pairs"]:
        first, second = pair["labelers"]
        if pair["n"]:
            print(f"{first} vs {second}: n={pair['n']}  QWK {pair['qwk']:.2f}  within ±1 {pair['within_one']:.0%}  exact {pair['exact']:.0%}")
        else:
            print(f"{first} vs {second}: no answers labeled by both")
    for name, ids in report["missing"].items():
        print(f"{name} did not label: {', '.join(ids)}")
    if report["disagreements"]:
        print("\ndisagreements (largest first):")
    for item in report["disagreements"]:
        print(f"  {item['id']} [{item['question_id']}]: " + "  ".join(f"{name}={score}" for name, score in item["scores"].items()))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nsaved {args.out}")
    return 0


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
    compare = commands.add_parser("import", help="compare two or more filled sheets")
    compare.add_argument("sheets", nargs="+", type=Path)
    compare.add_argument("--out", type=Path, help="also save the report as JSON")
    compare.add_argument("--dataset", type=Path, default=DATASET_FILE)
    compare.set_defaults(run=_cmd_import)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except SheetError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
