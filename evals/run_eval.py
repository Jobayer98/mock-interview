"""Grade the labeled dataset with the evaluator and report agreement/consistency.

    python evals/run_eval.py --runs 3 --workers 4
    python evals/run_eval.py --limit 6          # quick smoke run

The evaluator only ever receives question + rubric + answer. Labels and `reason` stay in this process.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.bank.loader import load_dataset, load_questions, summarize  # noqa: E402
from mockinterview.evaluate import metrics  # noqa: E402
from mockinterview.evaluate.evaluator import EvaluationError, evaluate_answer  # noqa: E402
from mockinterview.llm.claude_cli import ClaudeCliLLM  # noqa: E402

PASS_BARS = {"within_one": 0.80, "consistency_identical": 0.90}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="repeat each answer N times (consistency needs >= 2)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="only first N answers (smoke test)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    questions = {q["id"]: q for q in load_questions()}
    answers = load_dataset()
    if args.limit:
        answers = answers[: args.limit]
    print(summarize(answers))

    llm = ClaudeCliLLM(model=args.model)
    jobs = [(a, r) for a in answers for r in range(args.runs)]

    def run(job):
        a, r = job
        try:
            ev = evaluate_answer(llm, questions[a["question_id"]], a["answer"])
            return {"id": a["id"], "run": r, "score": ev.score, "confidence": ev.confidence,
                    "ungrounded": ev.ungrounded_quotes, "injection": ev.injection_attempt,
                    "latency_s": ev.llm.latency_s, "cost_usd": ev.llm.cost_usd, "model": ev.llm.model,
                    "signals_hit": [s.id for s in ev.signals if s.hit], "strengths": ev.strengths, "gaps": ev.gaps}
        except EvaluationError as exc:
            return {"id": a["id"], "run": r, "error": str(exc)}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, jobs))
    wall = time.perf_counter() - started

    ok = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    gold_by_id = {a["id"]: a["score"] for a in answers}
    reason_by_id = {a["id"]: a["reason"] for a in answers}

    # Metric 1: accuracy, using the median score across runs per answer.
    per_item: dict[str, list[int]] = {}
    for r in ok:
        per_item.setdefault(r["id"], []).append(r["score"])
    ids = [i for i in gold_by_id if i in per_item]
    pred = [int(median(per_item[i])) for i in ids]
    gold = [gold_by_id[i] for i in ids]

    report = {
        "when": datetime.now().isoformat(timespec="seconds"), "model": ok[0]["model"] if ok else None,
        "runs": args.runs, "items": len(ids), "errors": len(errors),
        "label_status": "draft labels (NOT human verified)" if any(a["label_status"] == "draft" for a in answers) else "reviewed",
        "accuracy": {
            "within_one": metrics.within_one(pred, gold), "exact": metrics.exact_match(pred, gold),
            "mae": metrics.mean_abs_error(pred, gold), "qwk": metrics.quadratic_weighted_kappa(pred, gold),
            "confusion_rows_human_cols_model": metrics.confusion(pred, gold),
        },
        "consistency": metrics.consistency([per_item[i] for i in ids]) if args.runs > 1 else None,
        "grounding": {"ungrounded_quotes_per_eval": mean(r["ungrounded"] for r in ok) if ok else None},
        "injection_flagged": sum(r["injection"] for r in ok),
        "ops": {"mean_latency_s": mean(r["latency_s"] for r in ok) if ok else None,
                "total_cost_usd": sum(r["cost_usd"] or 0 for r in ok), "wall_s": wall,
                "cost_per_eval_usd": mean(r["cost_usd"] or 0 for r in ok) if ok else None},
        "results": results,
    }

    out = Path(args.out) if args.out else ROOT / "evals" / "results" / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    acc = report["accuracy"]
    print(f"\nlabels: {report['label_status']} | model: {report['model']} | items: {len(ids)} | errors: {len(errors)}")
    print(f"within ±1: {acc['within_one']:.0%} (bar {PASS_BARS['within_one']:.0%})   exact: {acc['exact']:.0%}   "
          f"MAE: {acc['mae']:.2f}   QWK: {acc['qwk']:.2f}")
    print("confusion (rows=human 1..4, cols=model 1..4):")
    for i, row in enumerate(acc["confusion_rows_human_cols_model"], 1):
        print(f"  human {i}: {row}")
    if report["consistency"]:
        c = report["consistency"]
        print(f"consistency over {args.runs} runs: identical {c['identical']:.0%} (bar {PASS_BARS['consistency_identical']:.0%}), "
              f"within ±1 {c['within_one']:.0%}")
    print(f"ungrounded quotes/eval: {report['grounding']['ungrounded_quotes_per_eval']:.2f} | "
          f"injection flagged: {report['injection_flagged']} | mean latency {report['ops']['mean_latency_s']:.1f}s | "
          f"cost/eval ${report['ops']['cost_per_eval_usd']:.4f} | total ${report['ops']['total_cost_usd']:.2f}")

    bad = [(i, g, p) for i, g, p in zip(ids, gold, pred) if abs(g - p) >= 1]
    if bad:
        print("\ndisagreements (id, human, model, labeler reason):")
        for i, g, p in bad:
            print(f"  {i}: human {g} vs model {p} | {reason_by_id[i]}")
    for e in errors[:5]:
        print("ERROR", e["id"], e["error"][:200])
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
