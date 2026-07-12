"""Validate a results.json against the Track 2 output contract.

Usage: python scripts/validate_output.py <results.json> <tasks.json>

Checks, against the tasks that were requested:
  - results.json is a JSON array
  - every requested task_id appears exactly once
  - all four required style keys (plus any extra requested styles) are
    present with a non-empty string value — a missing key zeroes the clip
  - no unexpected style keys, no hyphenated style names
Exits 0 if valid, 1 with a list of problems otherwise.
"""

import json
import sys

REQUIRED_STYLES = ("formal", "sarcastic", "humorous_tech", "humorous_non_tech")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    results_path, tasks_path = sys.argv[1], sys.argv[2]

    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    problems = []

    if not isinstance(results, list):
        print("FAIL: results.json is not a JSON array")
        return 1

    by_id = {}
    for entry in results:
        if not isinstance(entry, dict) or "task_id" not in entry:
            problems.append(f"malformed result entry: {entry!r}")
            continue
        if entry["task_id"] in by_id:
            problems.append(f"duplicate task_id {entry['task_id']!r}")
        by_id[entry["task_id"]] = entry

    for task in tasks:
        tid = task["task_id"]
        wanted = list(task.get("styles") or [])
        wanted += [s for s in REQUIRED_STYLES if s not in wanted]
        entry = by_id.get(tid)
        if entry is None:
            problems.append(f"task {tid!r}: missing from results")
            continue
        captions = entry.get("captions")
        if not isinstance(captions, dict):
            problems.append(f"task {tid!r}: 'captions' missing or not an object")
            continue
        for style in wanted:
            value = captions.get(style)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"task {tid!r}: style {style!r} missing or empty")
        for key in captions:
            if key not in wanted:
                problems.append(f"task {tid!r}: unexpected style key {key!r}")
            if "-" in key:
                problems.append(f"task {tid!r}: hyphen in style key {key!r} (must use underscores)")

    if problems:
        print(f"FAIL: {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"OK: {results_path} is valid for {len(tasks)} task(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
