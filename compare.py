import argparse
import json
from pathlib import Path
from statistics import mean


def load_runs(path):
    runs = []
    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"Skipping invalid JSON at line {line_number}")
    return runs


def stage_times(run):
    totals = {}
    for hop in run.get("hops", []):
        for stage in hop.get("results", []):
            name = stage.get("stage")
            elapsed = stage.get("elapsed_ms")
            if name and isinstance(elapsed, (int, float)):
                totals.setdefault(name, []).append(elapsed)
    return {name: mean(values) for name, values in totals.items()}


def main():
    parser = argparse.ArgumentParser(
        description="Compare recent PathLens runs for performance regressions."
    )
    parser.add_argument("--history", default="pathlens_history.jsonl")
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument(
        "--threshold",
        type=float,
        default=50.0,
        help="Flag a stage when its average increases by this percentage.",
    )
    args = parser.parse_args()

    if args.last < 2:
        parser.error("--last must be at least 2")
    if args.threshold < 0:
        parser.error("--threshold cannot be negative")

    runs = load_runs(args.history)
    if len(runs) < 2:
        parser.error("Need at least two valid history records")

    runs = [r for r in runs if r.get("hops") and any(
        isinstance(hop.get("results"), list)
        and any(isinstance(item.get("elapsed_ms"), (int, float))
                for item in hop["results"])
        for hop in r["hops"]
    )]
    runs = runs[-args.last:]
    midpoint = len(runs) // 2
    baseline = runs[:midpoint]
    recent = runs[midpoint:]

    baseline_samples = [stage_times(run) for run in baseline]
    recent_samples = [stage_times(run) for run in recent]

    stage_names = sorted(
        set().union(*(sample.keys() for sample in baseline_samples),
                    *(sample.keys() for sample in recent_samples))
    )

    print("PATHLENS PERFORMANCE COMPARISON")
    print("=" * 36)
    print(f"Baseline runs: {len(baseline)}")
    print(f"Recent runs:   {len(recent)}")
    print(f"Regression threshold: {args.threshold:.1f}%\n")

    regressions = 0
    for stage in stage_names:
        before = [
            sample[stage] for sample in baseline_samples if stage in sample
        ]
        after = [
            sample[stage] for sample in recent_samples if stage in sample
        ]
        if not before or not after:
            continue

        old = mean(before)
        new = mean(after)
        change = ((new - old) / old * 100) if old > 0 else None

        if change is None:
            label = "N/A"
        elif change >= args.threshold and new > old:
            label = "REGRESSION"
            regressions += 1
        elif change <= -args.threshold:
            label = "IMPROVED"
        else:
            label = "STABLE"

        change_text = f"{change:+.1f}%" if change is not None else "N/A"
        print(
            f"{stage:18} {old:9.2f} ms -> {new:9.2f} ms"
            f"  ({change_text})  {label}"
        )

    print(f"\nPotential regressions: {regressions}")


if __name__ == "__main__":
    main()
