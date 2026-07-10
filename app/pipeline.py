"""Per-clip pipeline: download -> sample frames -> ground (Gemma vision) -> style (Gemma).

Every stage is bounded by a single per-clip deadline so no clip can exceed the
harness's 30-second per-clip limit.
"""

import glob
import json
import logging
import os
import re
import subprocess
import tempfile
import time

import requests
from google.genai import types

import llm

log = logging.getLogger("fourfaced.pipeline")

GROUND_MODEL = os.environ.get("GROUND_MODEL", "gemma-4-31b-it")
STYLE_MODEL = os.environ.get("STYLE_MODEL", "gemma-4-31b-it")
PER_CLIP_BUDGET = float(os.environ.get("PER_CLIP_BUDGET", "27"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024
# Seconds we insist on keeping in reserve for the two LLM stages: if the
# download alone would eat past this, we abort it early instead of starting
# LLM calls with no budget left.
LLM_RESERVE = 12.0

STYLE_GUIDES = {
    "formal": (
        "Polished, professional, neutral — like a caption under a photo in a "
        "news article or documentary. No jokes, no slang, no exclamation points."
    ),
    "sarcastic": (
        "Dry, deadpan, mocking wit. Technically true to the clip, but clearly "
        "unimpressed by it."
    ),
    "humorous_tech": (
        "Genuinely funny, built on technology / programming / internet-culture "
        "references (bugs, deploys, Wi-Fi, AI, software updates...) while still "
        "describing this clip."
    ),
    "humorous_non_tech": (
        "Genuinely funny with NO technology references at all — everyday-life "
        "humor, wordplay, or observational comedy about what's on screen."
    ),
}


class ClipError(Exception):
    pass


def download_video(url: str, dest: str, deadline: float) -> int:
    try:
        with requests.get(url, stream=True, timeout=(5, 10)) as r:
            r.raise_for_status()
            total = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ClipError(f"Video exceeds {MAX_DOWNLOAD_BYTES} bytes")
                    if time.monotonic() > deadline - LLM_RESERVE:
                        raise ClipError("Download would exhaust the clip's time budget")
            return total
    except requests.RequestException as e:
        raise ClipError(f"Download failed: {type(e).__name__}: {e}") from e


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return max(0.5, float(out))
    except Exception:
        log.warning("ffprobe failed for %s; assuming 10s", path)
        return 10.0


def sample_frames(path: str, duration: float, out_dir: str, deadline: float) -> list:
    # Roughly one frame every 4-5 seconds, clamped to 6..15 frames total.
    n = max(6, min(15, round(duration / 4.5)))
    fps = n / max(duration, 0.1)
    pattern = os.path.join(out_dir, "frame_%02d.jpg")
    timeout = max(3.0, deadline - time.monotonic() - LLM_RESERVE)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", path,
         "-vf", f"fps={fps},scale=-2:480", "-frames:v", str(n), "-q:v", "4",
         pattern],
        capture_output=True, timeout=timeout, check=True,
    )
    frames = sorted(glob.glob(os.path.join(out_dir, "frame_*.jpg")))
    if not frames:
        raise ClipError("ffmpeg produced no frames")
    return frames


def ground(frames: list, duration: float, deadline: float) -> str:
    """Vision stage: frames in, plain factual description out.

    Runs with thinking suppressed — perception doesn't need deliberation, and
    the saved seconds are what let the styling stage think.
    """
    prompt = (
        f"These {len(frames)} images are frames sampled evenly, in chronological "
        f"order, from one video clip about {duration:.0f} seconds long.\n"
        "In 90-140 words of plain prose, state only what is clearly visible in "
        "the clip: the main subject(s) and their actions; how the action changes "
        "over time; the setting; notable objects; lighting and weather; camera "
        "movement if apparent.\n"
        "Do not guess or embellish — omit anything ambiguous. Refer to it as "
        '"the clip" and never mention frames or images.'
    )
    contents = [prompt]
    for f in frames:
        with open(f, "rb") as fh:
            contents.append(types.Part.from_bytes(data=fh.read(), mime_type="image/jpeg"))
    return llm.generate(
        GROUND_MODEL,
        contents,
        deadline=deadline,
        max_tokens=1024,
        temperature=0.3,
        think=False,
    )


def extract_json(raw: str) -> dict:
    """Parse a JSON object out of a model response, tolerating code fences."""
    for candidate in (raw, re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        obj = json.loads(raw[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("No JSON object found in model response")


def build_style_prompt(facts: str, styles: list, strict: bool = False) -> str:
    guide_lines = "\n".join(
        f'- "{s}": {STYLE_GUIDES.get(s, "Write in a " + s.replace("_", " ") + " tone.")}'
        for s in styles
    )
    keys_example = "{" + ", ".join(f'"{s}": "..."' for s in styles) + "}"
    prompt = (
        "Facts observed from a short video clip:\n\n"
        f"FACTS:\n{facts}\n\n"
        f"Write one caption for the clip in each of these styles:\n{guide_lines}\n\n"
        "Each caption: 1-2 sentences of English, consistent with the FACTS — "
        "invent no people, animals, objects, actions, or places that are not in "
        "them. Check each caption against the FACTS before finalizing, and make "
        "the styles read clearly differently from each other.\n"
        f"Respond with ONLY this JSON object, nothing else: {keys_example}"
    )
    if strict:
        prompt += (
            "\n\nIMPORTANT: your previous answer was not valid JSON with exactly "
            f"those keys. Respond with nothing but a single JSON object whose keys "
            f"are exactly: {json.dumps(styles)}. Every value must be a non-empty "
            "English caption string."
        )
    return prompt


# Seconds reserved for a no-think rescue pass if the thinking pass fails or
# runs long. The thinking attempt is only made when the budget can absorb it.
STYLE_RESCUE_RESERVE = 7.0
STYLE_THINK_MIN_BUDGET = 18.0


def style_captions(facts: str, styles: list, deadline: float, strict: bool = False) -> tuple:
    """Style the grounded facts. Returns (captions_dict, used_thinking).

    Gemma 4's thinking is where drafts get checked against the facts, so a
    thinking pass is preferred — but free-tier latency is variable, so the
    thinking attempt gets a sub-deadline that always leaves room for a fast
    no-think rescue pass.
    """
    prompt = build_style_prompt(facts, styles, strict=strict)
    if deadline - time.monotonic() >= STYLE_THINK_MIN_BUDGET:
        try:
            raw = llm.generate(
                STYLE_MODEL, [prompt],
                deadline=deadline - STYLE_RESCUE_RESERVE,
                max_tokens=3072, temperature=0.85, think=True,
            )
            return extract_json(raw), True
        except Exception as e:
            log.warning("Thinking styling pass failed (%s); rescuing without thinking", e)
    raw = llm.generate(
        STYLE_MODEL, [prompt],
        deadline=deadline,
        max_tokens=1024, temperature=0.85, think=False,
    )
    return extract_json(raw), False


def process_task(task: dict) -> tuple:
    """Run the full pipeline for one task. Returns (captions, meta).

    Raises ClipError/Exception on failure — the caller owns fallbacks. `meta`
    carries the grounding facts and timings for logging and the results UI.
    """
    styles = task.get("styles") or []
    deadline = time.monotonic() + PER_CLIP_BUDGET
    meta = {"task_id": task.get("task_id"), "facts": None, "timings": {}}
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="fourfaced_") as tmp:
        video_path = os.path.join(tmp, "clip.mp4")
        size = download_video(task["video_url"], video_path, deadline)
        meta["timings"]["download_s"] = round(time.monotonic() - t0, 1)
        meta["video_bytes"] = size

        duration = probe_duration(video_path)
        meta["duration_s"] = round(duration, 1)

        t1 = time.monotonic()
        frames = sample_frames(video_path, duration, tmp, deadline)
        meta["timings"]["frames_s"] = round(time.monotonic() - t1, 1)
        meta["frame_count"] = len(frames)

        t2 = time.monotonic()
        facts = ground(frames, duration, deadline)
        meta["facts"] = facts
        meta["timings"]["ground_s"] = round(time.monotonic() - t2, 1)

    t3 = time.monotonic()
    captions, used_thinking = style_captions(facts, styles, deadline)
    meta["style_thinking"] = used_thinking
    meta["timings"]["style_s"] = round(time.monotonic() - t3, 1)
    meta["timings"]["total_s"] = round(time.monotonic() - t0, 1)
    return captions, meta
