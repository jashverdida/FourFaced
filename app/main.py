"""FourFaced — AMD Developer Hackathon ACT II, Track 2: Video Captioning.

Container entrypoint. Reads /input/tasks.json, produces styled captions for
each task, writes /output/results.json, exits 0.

Phase 1: harness shell only — placeholder captions, no AI calls yet.
"""

import json
import logging
import os
import sys
import time

INPUT_PATH = os.environ.get("INPUT_PATH", "/input/tasks.json")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/output/results.json")

log = logging.getLogger("fourfaced")


def caption_task(task: dict) -> dict:
    """Produce a caption for every requested style of one task.

    Phase 1: placeholder captions only. Later phases replace this with the
    real ground-then-style pipeline. Must never raise for a single bad task —
    the caller also guards, but each style gets a value no matter what.
    """
    styles = task.get("styles") or []
    return {style: "[placeholder caption]" for style in styles}


def process_tasks(tasks: list) -> list:
    results = []
    for task in tasks:
        if not isinstance(task, dict):
            log.error("Skipping malformed task entry (not an object): %r", task)
            continue
        task_id = task.get("task_id")
        if not task_id:
            log.error("Skipping task with no task_id: %r", task)
            continue
        started = time.monotonic()
        try:
            captions = caption_task(task)
        except Exception:
            log.exception("Task %s failed; emitting fallback captions", task_id)
            captions = {style: "[caption unavailable]" for style in task.get("styles") or []}
        log.info("Task %s done in %.1fs (%d styles)",
                 task_id, time.monotonic() - started, len(captions))
        results.append({"task_id": task_id, "captions": captions})
    return results


def write_results(results: list) -> None:
    out_dir = os.path.dirname(OUTPUT_PATH)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # Write to a temp file first so /output/results.json is never left half-written.
    tmp_path = OUTPUT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, OUTPUT_PATH)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    run_started = time.monotonic()
    log.info("FourFaced starting. input=%s output=%s", INPUT_PATH, OUTPUT_PATH)

    try:
        with open(INPUT_PATH, "r", encoding="utf-8") as f:
            tasks = json.load(f)
    except Exception:
        log.exception("Could not read or parse %s — unrecoverable", INPUT_PATH)
        return 1

    if not isinstance(tasks, list):
        log.error("%s is not a JSON array — unrecoverable", INPUT_PATH)
        return 1

    results = process_tasks(tasks)

    try:
        write_results(results)
    except Exception:
        log.exception("Could not write %s — unrecoverable", OUTPUT_PATH)
        return 1

    log.info("Wrote %d results in %.1fs total. Exiting 0.",
             len(results), time.monotonic() - run_started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
