import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def load_history(path):
    records = []

    with Path(path).open(encoding="utf-8") as history_file:
        for line_number, line in enumerate(history_file, start=1):
            if not line.strip():
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                print(
                    f"Skipping invalid JSON on line {line_number}: {error}"
                )

    return records


def summarize(records, last):
    records = records[-last:] if last else records

    if not records:
        print("No probe history found.")
        return

    outcomes = Counter(
        record.get("overall_outcome", "UNKNOWN")
        for record in records
    )

    stage_timings = defaultdict(list)
    http_statuses = Counter()
    ttbf_values = []
    targets = Counter()

    for record in records:
        targets[record.get("target", "UNKNOWN")] += 1

        for hop in record.get("hops", []):
            status = hop.get("status_code")
            if status is not None:
                http_statuses[status] += 1

            for result in hop.get("results", []):
                stage = result.get("stage")
                elapsed = result.get("elapsed_ms")

                if stage and isinstance(elapsed, (int, float)):
                    stage_timings[stage].append(elapsed)

                if (
                    stage == "HTTP_REQUEST"
                    and isinstance(
                        result.get("time_to_first_byte_ms"),
                        (int, float),
                    )
                ):
                    ttbf_values.append(
                        result["time_to_first_byte_ms"]
                    )

    successful = outcomes.get("SUCCESS", 0)
    success_rate = successful / len(records) * 100

    print("PATHLENS HISTORY SUMMARY")
    print("=" * 32)
    print(f"Runs analyzed: {len(records)}")
    print(f"Successful runs: {successful}")
    print(f"Failed/other runs: {len(records) - successful}")
    print(f"Success rate: {success_rate:.1f}%")

    print("\nOverall outcomes:")
    for outcome, count in outcomes.most_common():
        print(f"  {outcome}: {count}")

    print("\nTargets:")
    for target, count in targets.most_common():
        print(f"  {target}: {count} runs")

    print("\nHTTP statuses:")
    if http_statuses:
        for status, count in sorted(http_statuses.items()):
            print(f"  {status}: {count}")
    else:
        print("  No HTTP responses recorded")

    print("\nAverage stage timings:")
    for stage, values in sorted(stage_timings.items()):
        print(f"  {stage}: {mean(values):.2f} ms")

    if ttbf_values:
        print(
            "\nAverage HTTP time to first byte: "
            f"{mean(ttbf_values):.2f} ms"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Analyze PathLens JSONL history"
    )
    parser.add_argument(
        "--history",
        default="pathlens_history.jsonl",
        help="Path to the JSONL history file",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        help="Analyze only the most recent N runs; 0 means all",
    )

    args = parser.parse_args()

    if args.last < 0:
        parser.error("--last must be zero or greater")

    path = Path(args.history)

    if not path.exists():
        parser.error(f"History file not found: {path}")

    summarize(load_history(path), args.last)


if __name__ == "__main__":
    main()
