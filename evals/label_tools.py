"""Human labeling of the eval dataset: export blind sheets, compare labelers, write consensus scores.

    python evals/label_tools.py export
    python evals/label_tools.py import evals/labels/alice.csv evals/labels/bob.csv
    python evals/label_tools.py adjudicate evals/labels/alice.csv evals/labels/bob.csv [--resolved evals/labels/resolved.csv]

Sheets never contain draft scores or reasons, so labelers stay independent of the Claude-written drafts (I10).
Filled sheets can hold real candidates' answers, so they stay out of git.
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.bank.loader import DATASET_FILE, SCORES, load_dataset, load_questions  # noqa: E402
from mockinterview.evaluate import metrics  # noqa: E402

SHEET_COLUMNS = ["id", "question", "rubric_summary", "answer", "score"]
DEFAULT_SHEET = ROOT / "evals" / "label_sheet.csv"
MIN_LABELERS = 2
LABELER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_FORMULA_STARTS = ("=", "+", "-", "@")
_ITEM_START = re.compile(r"^(\s*)- id:\s*(\S+)\s*$")


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


def consensus(scores: dict[str, int]) -> int | None:
    """The score more than half the labelers gave. Two labelers must therefore match exactly, and nothing is ever averaged."""
    score, votes = Counter(scores.values()).most_common(1)[0]
    return score if votes * 2 > len(scores) else None


def _edited_item(block: list[str], indent: str, newline: str, update: dict, draft_score: int | None) -> list[str]:
    block = list(block)

    def position(key: str) -> int | None:
        prefix = f"{indent}{key}:"  # answer text is indented deeper, so words inside it cannot match
        return next((i for i, line in enumerate(block) if line.startswith(prefix)), None)

    def put(key: str, value, after: str) -> None:
        line, at = f"{indent}{key}: {value}{newline}", position(key)
        if at is not None:
            block[at] = line
            return
        anchor = position(after)
        if anchor is None:
            raise SheetError(f"dataset item has no {after!r} line: {block[0].strip()}")
        block.insert(anchor + 1, line)

    put("score", update["score"], after="question_id")
    if draft_score is not None:
        put("draft_score", draft_score, after="score")
    put("label_status", "reviewed", after="score")
    put("labelers", json.dumps(update["labelers"]), after="label_status")
    return block


def _rewrite(text: str, document: dict, updates: dict[str, dict]) -> str:
    """Change only the lines that must change, so comments and folded answers survive, then prove the result means what was intended."""
    newline = "\r\n" if "\r\n" in text else "\n"
    by_id = {answer["id"]: answer for answer in document["answers"]}
    lines = text.splitlines(keepends=True)
    found = [(i, m) for i, line in enumerate(lines) if (m := _ITEM_START.match(line)) and m.group(2) in by_id]
    item_indent = found[0][1].group(1) if found else ""
    starts = [(i, m) for i, m in found if m.group(1) == item_indent]
    for position, (start, match) in reversed(list(enumerate(starts))):
        item = match.group(2)
        if item in updates:
            end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
            draft_score = by_id[item]["score"] if by_id[item]["label_status"] == "draft" else None
            lines[start:end] = _edited_item(lines[start:end], item_indent + "  ", newline, updates[item], draft_score)
    result = "".join(lines)

    expected = copy.deepcopy(document)
    for answer in expected["answers"]:
        if answer["id"] in updates:
            if answer["label_status"] == "draft":
                answer["draft_score"] = answer["score"]
            answer.update(score=updates[answer["id"]]["score"], label_status="reviewed", labelers=updates[answer["id"]]["labelers"])
    if yaml.safe_load(result) != expected:
        raise SheetError("the dataset could not be updated safely, so nothing was written")
    return result


def adjudicate(sheets: dict[str, dict[str, int]], dataset: Path = DATASET_FILE, resolutions: dict[str, int] | None = None, *,
               dry_run: bool = False) -> dict:
    """Write human consensus into the dataset. Disputed answers change only with a recorded resolution; the grader's scores play no part."""
    resolutions = resolutions or {}
    text = dataset.read_bytes().decode("utf-8")
    document = yaml.safe_load(text)
    updates: dict[str, dict] = {}
    unresolved, too_few, changed, resolved = [], [], [], set()
    for answer in document["answers"]:
        item, scores = answer["id"], _scores_for(answer["id"], sheets)
        if not scores:
            continue
        if len(scores) < MIN_LABELERS:
            too_few.append(item)
            continue
        final = consensus(scores)
        if final is None:
            if item not in resolutions:
                unresolved.append({"id": item, "scores": scores})
                continue
            final = resolutions[item]
            resolved.add(item)
        updates[item] = {"score": final, "labelers": sorted(scores)}
        if final != answer.get("draft_score", answer["score"]):
            changed.append(item)
    stray = sorted(set(resolutions) - resolved)
    if stray:
        raise SheetError(f"resolutions were given for answers that are not in dispute: {', '.join(stray)}")
    new_text = _rewrite(text, document, updates)
    if updates and not dry_run:
        dataset.write_bytes(new_text.encode("utf-8"))
    return {"reviewed": sorted(updates), "changed_from_draft": sorted(changed), "unresolved": unresolved, "too_few_labels": too_few, "written": bool(updates) and not dry_run}


def _cmd_adjudicate(args: argparse.Namespace) -> int:
    known_ids = {a["id"] for a in load_dataset(args.dataset)}
    resolutions = _read_scores(args.resolved, known_ids) if args.resolved else None
    report = adjudicate(read_sheets(args.sheets, args.dataset), args.dataset, resolutions, dry_run=args.dry_run)
    print(f"{'would mark' if args.dry_run else 'marked'} {len(report['reviewed'])} answers reviewed; {len(report['changed_from_draft'])} differ from the draft score")
    for item in report["unresolved"]:
        print(f"  unresolved {item['id']}: " + "  ".join(f"{name}={score}" for name, score in item["scores"].items()) + " (add it to a --resolved sheet after discussion)")
    if report["too_few_labels"]:
        print(f"  fewer than {MIN_LABELERS} labels, left as they were: {', '.join(report['too_few_labels'])}")
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
    settle = commands.add_parser("adjudicate", help="write consensus scores into the dataset")
    settle.add_argument("sheets", nargs="+", type=Path)
    settle.add_argument("--resolved", type=Path, help="CSV with id and score columns, for answers settled by discussion")
    settle.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    settle.add_argument("--dataset", type=Path, default=DATASET_FILE)
    settle.set_defaults(run=_cmd_adjudicate)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except SheetError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
