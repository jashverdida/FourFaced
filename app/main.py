"""FourFaced — AMD Developer Hackathon ACT II, Track 2: Video Captioning.

Container entrypoint. Reads /input/tasks.json, runs the ground-then-style
Gemma pipeline per clip, writes /output/results.json, exits 0.

One clip. Four faces. Every caption backed by what's actually on screen.
"""

import json
import logging
import os
import sys
import time


def load_dotenv():
    """Load repo-root .env for local runs; container gets real env via -e."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_dotenv()

import pipeline  # noqa: E402  (reads env at import time)

INPUT_PATH = os.environ.get("INPUT_PATH", "/input/tasks.json")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/output/results.json")

log = logging.getLogger("fourfaced")

# Last-resort captions when the pipeline fails before grounding produced any
# facts. Deliberately generic — never tuned to specific clip content.
LAST_RESORT = {
    "formal": "A short video clip depicting a scene recorded on camera.",
    "sarcastic": "Ah yes, another video clip. Riveting stuff, truly.",
    "humorous_tech": "This clip loaded faster than my last software update, and that's saying something.",
    "humorous_non_tech": "A video so mysterious even the person who filmed it is probably still confused.",
}


def fallback_captions(styles: list) -> dict:
    return {s: LAST_RESORT.get(s, "A short video clip.") for s in styles}


def process_tasks(tasks: list) -> tuple:
    results, debug = [], []
    for task in tasks:
        if not isinstance(task, dict):
            log.error("Skipping malformed task entry (not an object): %r", task)
            continue
        task_id = task.get("task_id")
        if not task_id:
            log.error("Skipping task with no task_id: %r", task)
            continue
        styles = pipeline.task_styles(task)
        started = time.monotonic()
        meta = {"task_id": task_id}
        try:
            captions, meta = pipeline.process_task(task)
            meta["fallback_used"] = bool(meta.get("template_styles"))
        except Exception as e:
            # Pipeline failed before grounding produced facts (download,
            # ffmpeg, or grounding itself) — generic captions, never a gap.
            log.exception("Task %s failed; emitting generic fallback captions", task_id)
            captions = fallback_captions(styles)
            meta["fallback_used"] = True
            meta["last_resort"] = True
            meta["error"] = f"{type(e).__name__}: {e}"
        elapsed = time.monotonic() - started
        log.info("Task %s done in %.1fs (%d styles)", task_id, elapsed, len(captions))
        if elapsed >= 0.8 * pipeline.PER_CLIP_BUDGET:
            log.warning("Task %s took %.1fs - %d%% of the %.0fs clip budget; "
                        "investigate if this recurs", task_id, elapsed,
                        100 * elapsed / pipeline.PER_CLIP_BUDGET, pipeline.PER_CLIP_BUDGET)
        results.append({"task_id": task_id, "captions": captions})
        debug.append(meta)
        # Persist after every task so a crash or global timeout mid-batch
        # still leaves valid results for the clips already done.
        try:
            write_json(OUTPUT_PATH, results)
        except Exception:
            log.exception("Incremental write of %s failed", OUTPUT_PATH)
    return results, debug


def write_json(path: str, data) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # Write to a temp file first so the output is never left half-written.
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_started = time.monotonic()
    log.info("FourFaced starting. input=%s output=%s ground=%s style=%s",
             INPUT_PATH, OUTPUT_PATH, pipeline.GROUND_MODEL, pipeline.STYLE_MODEL)

    try:
        with open(INPUT_PATH, "r", encoding="utf-8") as f:
            tasks = json.load(f)
    except Exception:
        log.exception("Could not read or parse %s — unrecoverable", INPUT_PATH)
        return 1

    if not isinstance(tasks, list):
        log.error("%s is not a JSON array — unrecoverable", INPUT_PATH)
        return 1

    results, debug = process_tasks(tasks)

    # Caption-source summary: the pre-submission health check. "Fully styled"
    # is the real pipeline output; anything else is a fallback rung and gets
    # a per-task warning with the reason.
    styled = [d for d in debug if d.get("facts") and not d.get("template_styles")]
    templated = [d for d in debug if d.get("facts") and d.get("template_styles")]
    generic = [d for d in debug if not d.get("facts")]
    summary_log = log.info if not templated and not generic else log.warning
    summary_log("Caption sources: %d/%d fully styled, %d template-styled "
                "(grounded facts, styling fell back), %d generic (grounding failed)",
                len(styled), len(debug), len(templated), len(generic))
    for d in templated:
        log.warning("Task %s: grounded, but styling fell back to facts template "
                    "for %s", d.get("task_id"), d.get("template_styles"))
    for d in generic:
        log.warning("Task %s: NO grounding - generic captions. Cause: %s",
                    d.get("task_id"), d.get("error", "see traceback above"))

    try:
        write_json(OUTPUT_PATH, results)
    except Exception:
        log.exception("Could not write %s — unrecoverable", OUTPUT_PATH)
        return 1

    # Grounding facts and timings, as evidence for the results UI. Written
    # beside results.json; the grading harness only reads results.json.
    try:
        write_json(os.path.join(os.path.dirname(OUTPUT_PATH) or ".", "fourfaced_debug.json"), debug)
    except Exception:
        log.exception("Could not write debug file (non-fatal)")

    log.info("Wrote %d results in %.1fs total. Exiting 0.",
             len(results), time.monotonic() - run_started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
