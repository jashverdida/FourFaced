"""Per-clip pipeline: download -> sample frames -> ground (Gemma vision) -> style (Gemma).

Every stage is bounded by a single per-clip deadline so no clip can exceed the
harness's 30-second per-clip limit.
"""

import glob
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import zlib

import requests
from google.genai import types

import llm

log = logging.getLogger("fourfaced.pipeline")

GROUND_MODEL = os.environ.get("GROUND_MODEL", "gemma-4-31b-it")
STYLE_MODEL = os.environ.get("STYLE_MODEL", "gemma-4-31b-it")
PER_CLIP_BUDGET = float(os.environ.get("PER_CLIP_BUDGET", "27"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
# Frame-sampling ceiling, tuned to the 27s clip budget: grounding payloads
# above ~15 frames risk eating the styling stage's share. Raising it trades
# clip-budget margin for denser sampling on long clips.
MAX_FRAMES = int(os.environ.get("MAX_FRAMES", "15"))

MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024
# Seconds we insist on keeping in reserve for the two LLM stages: if the
# download alone would eat past this, we abort it early instead of starting
# LLM calls with no budget left.
LLM_RESERVE = 12.0

# The four style keys the rules require in every clip's output. A missing key
# zeroes the whole clip, so captions are always generated for all of these —
# even if a task's "styles" list omits some — plus any extra styles requested.
REQUIRED_STYLES = ("formal", "sarcastic", "humorous_tech", "humorous_non_tech")


def task_styles(task: dict) -> list:
    """Styles to caption for a task: the requested ones (in request order)
    plus any of the four required styles the request left out."""
    requested = [s for s in (task.get("styles") or []) if isinstance(s, str) and s]
    return requested + [s for s in REQUIRED_STYLES if s not in requested]


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
    # file:// URIs are supported for local testing (e.g. tests/extra_clips)
    # so the same code path exercises local and remote clips alike; the real
    # competition harness only ever supplies http(s) URLs.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        src = urllib.parse.unquote(parsed.path)
        if os.name == "nt" and src.startswith("/") and len(src) > 2 and src[2] == ":":
            src = src[1:]
        size = os.path.getsize(src)
        if size > MAX_DOWNLOAD_BYTES:
            raise ClipError(f"Video exceeds {MAX_DOWNLOAD_BYTES} bytes")
        shutil.copyfile(src, dest)
        return size
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


def probe_duration(path: str, deadline: float) -> float:
    # Timeout tied to the clip deadline (was a fixed 10s that could overshoot
    # it): prefer leaving the LLM reserve intact, floor 2s so the probe stays
    # viable, and on failure fall back to assuming 10s as before.
    timeout = max(2.0, min(10.0, deadline - time.monotonic() - LLM_RESERVE))
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=timeout, check=True,
        ).stdout.strip()
        return max(0.5, float(out))
    except Exception:
        log.warning("ffprobe failed for %s; assuming 10s", path)
        return 10.0


def probe_resolution(path: str, deadline: float) -> tuple:
    """Return (width, height) of the first video stream, or (0, 0) on failure."""
    timeout = max(2.0, min(10.0, deadline - time.monotonic() - LLM_RESERVE))
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", path],
            capture_output=True, text=True, timeout=timeout, check=True,
        ).stdout.strip()
        width, height = out.split("x")
        return int(width), int(height)
    except Exception:
        log.warning("ffprobe resolution failed for %s; assuming unknown", path)
        return 0, 0


def sample_frames(path: str, duration: float, out_dir: str, deadline: float) -> list:
    width, height = probe_resolution(path, deadline)
    high_res = height > 720
    long_clip = duration > 15.0
    needs_downscale = high_res or long_clip

    if needs_downscale:
        # Heavy clips: cap frames and downscale to 480p to protect the
        # vision-model payload size and the 27-second clip budget.
        n = 6
    else:
        # Existing cadence for lighter clips: ~1 frame every 4 seconds,
        # clamped to 6..MAX_FRAMES.
        n = max(6, min(MAX_FRAMES, math.ceil(duration / 4.0)))

    fps = n / max(duration, 0.1)
    pattern = os.path.join(out_dir, "frame_%02d.jpg")

    if needs_downscale:
        vf = f"fps={fps},scale=-2:480"
    else:
        vf = f"fps={fps}"

    # Prefer leaving the LLM reserve intact (3s viability floor), but never
    # let the floor extend past the clip deadline itself — better to fail
    # fast into the fallback ladder than to blow the hard cap.
    remaining = deadline - time.monotonic()
    timeout = min(max(3.0, remaining - LLM_RESERVE), max(0.5, remaining - 0.5))
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", path,
         "-vf", vf, "-frames:v", str(n), "-q:v", "4",
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
        "In 90-140 words of plain English prose (English only, regardless of "
        "any language spoken or written in the clip), state only what is "
        "clearly visible in "
        "the clip: the main subject(s) and their actions; how the action changes "
        "over time; the setting; notable objects; lighting and weather; camera "
        "movement if apparent.\n"
        "Do not guess or embellish — omit anything ambiguous. Refer to it as "
        '"the clip" and never mention frames or images.'
    )
    # Images must precede the text prompt for Gemma's vision parser.
    contents = []
    for f in frames:
        with open(f, "rb") as fh:
            contents.append(types.Part.from_bytes(data=fh.read(), mime_type="image/jpeg"))
    contents.append(prompt)
    return llm.generate(
        GROUND_MODEL,
        contents,
        deadline=deadline,
        # The prompt asks for 90-140 words (~200 tokens); 256 caps the
        # worst-case generation tail without ever truncating a valid answer.
        max_tokens=256,
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


# Minimum budget left before attempting the thinking refinement pass (must
# exceed the API's 10s minimum request deadline plus the safety margin).
STYLE_REFINE_MIN_BUDGET = 15.0
# The refinement pass runs against its own bounded slice of the clip budget
# — min(remaining - margin, cap) — so a slow thinking pass can never consume
# the whole tail of the clip. Its failure always keeps the drafts.
STYLE_REFINE_CAP = 15.0
STYLE_REFINE_MARGIN = 2.0


def style_captions(facts: str, styles: list, deadline: float, strict: bool = False) -> dict:
    """Draft pass: facts to styled captions, thinking suppressed for speed.

    This is the quality floor — it must succeed fast. The thinking pass runs
    afterwards as refine_captions(), a pure upgrade that can never cost us
    the clip.
    """
    raw = llm.generate(
        STYLE_MODEL,
        [build_style_prompt(facts, styles, strict=strict)],
        deadline=deadline,
        # Four 1-2 sentence captions as JSON is ~150-250 tokens; 512 caps the
        # generation tail with ample headroom.
        max_tokens=512, temperature=0.85, think=False,
    )
    return extract_json(raw)


def refine_captions(facts: str, captions: dict, styles: list, deadline: float) -> dict:
    """Thinking pass: Gemma reviews the draft captions against the facts.

    Returns the refined captions, or raises — callers keep the drafts on any
    failure, so a timeout here can never hurt the clip. Internally bounded to
    a STYLE_REFINE_CAP slice of the remaining budget (single attempt), so it
    can never run the clip to its deadline either.
    """
    deadline = min(deadline - STYLE_REFINE_MARGIN,
                   time.monotonic() + STYLE_REFINE_CAP)
    guide_lines = "\n".join(
        f'- "{s}": {STYLE_GUIDES.get(s, "a " + s.replace("_", " ") + " tone")}'
        for s in styles)
    keys_example = "{" + ", ".join(f'"{s}": "..."' for s in styles) + "}"
    prompt = (
        "Facts observed from a short video clip:\n\n"
        f"FACTS:\n{facts}\n\n"
        f"Draft captions for the clip:\n{json.dumps({s: captions[s] for s in styles})}\n\n"
        f"Style definitions:\n{guide_lines}\n\n"
        "Review each draft against the FACTS: fix anything the facts do not "
        "support, sharpen weak style execution, and keep each caption 1-2 "
        "English sentences. If a draft is already accurate and on-style, keep it.\n"
        f"Respond with ONLY the final JSON object, nothing else: {keys_example}"
    )
    raw = llm.generate(
        STYLE_MODEL, [prompt],
        deadline=deadline,
        max_tokens=3072, temperature=0.6, think=True,
    )
    refined = extract_json(raw)
    if missing_styles(refined, styles):
        raise ValueError("refinement dropped styles; keeping drafts")
    return {s: refined[s].strip() for s in styles}


def missing_styles(captions, styles: list) -> list:
    """Requested styles that lack a non-empty string caption."""
    if not isinstance(captions, dict):
        return list(styles)
    return [s for s in styles
            if not isinstance(captions.get(s), str) or not captions[s].strip()]


_FACTS_LEADIN = re.compile(
    r"^(the|this)\s+(clip|video|footage|scene)\s+"
    r"(shows|features|depicts|captures|presents|portrays|opens\s+with|"
    r"begins\s+with|starts\s+with|is\s+set\s+in|is)\s+", re.I)

# Clip-noun opener that survived _FACTS_LEADIN (e.g. "the clip takes place
# ..."), for voiced templates to rewrite into natural phrasing.
_FACTS_CLIP_NOUN = re.compile(r"^(the|this)\s+(clip|video|footage|scene)\s+", re.I)


def _facts_subject(facts: str, max_len: int = 140) -> str:
    """First sentence of the facts, without a 'The clip shows' lead-in."""
    sentence = facts.strip().split(". ")[0].strip().rstrip(".")
    sentence = _FACTS_LEADIN.sub("", sentence)
    if len(sentence) > max_len:
        sentence = sentence[:max_len].rsplit(" ", 1)[0].rstrip(",;:")
    return sentence or "a short scene recorded on camera"


# Per-style wrapper pools for template_captions, picked deterministically per
# clip (crc32 of the facts) so reruns of one clip are stable while different
# clips vary. Wrappers may joke about the video-as-artifact only — never add
# on-screen claims, since the subject phrase is the only footage-derived part.
_TEMPLATE_VARIANTS = {
    "sarcastic": (
        "Oh look: {lower}. Groundbreaking content, truly.",
        "Ah yes, {lower} — exactly the thrill ride the internet was missing.",
        "Hold the applause: {lower}. Cinema may never recover.",
    ),
    "humorous_tech": (
        "Status update: {lower} — everything running smoothly, zero crashes reported.",
        "Deploy log: {lower}. Zero bugs found, which is more than most software can claim.",
        "System notification: {lower} — buffering complete, drama still loading.",
    ),
    "humorous_non_tech": (
        "And in today's episode of things that happened: {lower}.",
        "Witnessed today, entirely free of charge: {lower}.",
        "Somewhere out there, someone filmed this so we didn't have to: {lower}.",
    ),
}


def template_captions(facts: str, styles: list) -> dict:
    """Deterministic captions built straight from the grounding facts.

    Last rung of the ladder before generic captions — used when the styling
    model is unavailable or the clip is out of time budget. Formal carries
    the most facts; the voiced styles wrap the subject in one of a few
    style-distinct framings.
    """
    subject = _facts_subject(facts)
    upper = subject[0].upper() + subject[1:]
    lower = subject[0].lower() + subject[1:]
    # Facts like "The clip takes place at night..." survive the lead-in strip
    # noun and all; inside a voiced wrapper ("Oh look: the clip takes place
    # ...") that reads broken, so voiced styles swap the clip-noun opener for
    # a natural one. Formal keeps the original phrasing.
    voiced = _FACTS_CLIP_NOUN.sub("the whole thing ", lower)

    # Formal is the informative one: append the second facts sentence when it
    # fits, so it reads like a news-style caption rather than a stub.
    formal = f"{upper}."
    sentences = [s.strip() for s in facts.strip().split(". ") if s.strip()]
    if len(sentences) > 1:
        second = sentences[1].rstrip(".")
        if second and len(formal) + len(second) + 2 <= 220:
            formal = f"{formal} {second[0].upper()}{second[1:]}."

    variant = zlib.crc32(facts.strip().encode("utf-8"))
    captions = {}
    for s in styles:
        pool = _TEMPLATE_VARIANTS.get(s)
        if pool:
            captions[s] = pool[variant % len(pool)].format(lower=voiced)
        elif s == "formal":
            captions[s] = formal
        else:
            captions[s] = f"{upper}."
    return captions


def process_task(task: dict) -> tuple:
    """Run the full pipeline for one task. Returns (captions, meta).

    Raises ClipError/Exception on failure — the caller owns fallbacks. `meta`
    carries the grounding facts and timings for logging and the results UI.
    """
    styles = task_styles(task)
    deadline = time.monotonic() + PER_CLIP_BUDGET
    meta = {"task_id": task.get("task_id"), "facts": None, "timings": {}}
    t0 = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="fourfaced_") as tmp:
        video_path = os.path.join(tmp, "clip.mp4")
        size = download_video(task["video_url"], video_path, deadline)
        meta["timings"]["download_s"] = round(time.monotonic() - t0, 1)
        meta["video_bytes"] = size

        duration = probe_duration(video_path, deadline)
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
    captions = {}
    try:
        captions = style_captions(facts, styles, deadline)
    except Exception as e:
        log.warning("Task %s: styling failed (%s)", meta["task_id"], e)

    # Validation ladder: strict-JSON retry, then facts-derived templates, so
    # every requested style always has a non-empty caption.
    missing = missing_styles(captions, styles)
    meta["strict_retry"] = False
    if missing and deadline - time.monotonic() >= 10:
        meta["strict_retry"] = True
        log.warning("Task %s: styles %s missing/invalid; strict retry",
                    meta["task_id"], missing)
        try:
            retry_captions = style_captions(facts, styles, deadline, strict=True)
            for s in missing_styles(captions, styles):
                if s not in missing_styles(retry_captions, [s]):
                    captions[s] = retry_captions[s]
        except Exception as e:
            log.warning("Task %s: strict retry failed (%s)", meta["task_id"], e)

    still_missing = missing_styles(captions, styles)
    meta["template_styles"] = still_missing
    if still_missing:
        log.warning("Task %s: filling styles %s from facts template",
                    meta["task_id"], still_missing)
        fills = template_captions(facts, still_missing)
        for s in still_missing:
            captions[s] = fills[s]
    captions = {s: captions[s].strip() for s in styles}
    meta["timings"]["style_s"] = round(time.monotonic() - t3, 1)

    # Upgrade pass: Gemma's thinking reviews the drafts against the facts.
    # Only attempted with clean LLM drafts and budget to spare; any failure
    # keeps the drafts.
    meta["style_thinking"] = False
    refine_budget = deadline - time.monotonic()
    if not still_missing and refine_budget >= STYLE_REFINE_MIN_BUDGET:
        t4 = time.monotonic()
        try:
            captions = refine_captions(facts, captions, styles, deadline)
            meta["style_thinking"] = True
        except Exception as e:
            log.info("Task %s: refinement pass skipped/failed (%s); keeping drafts",
                     meta["task_id"], e)
        meta["timings"]["refine_s"] = round(time.monotonic() - t4, 1)
    elif not still_missing:
        log.info("Task %s: refinement skipped - %.1fs of budget left (needs >= %.0fs)",
                 meta["task_id"], refine_budget, STYLE_REFINE_MIN_BUDGET)

    meta["timings"]["total_s"] = round(time.monotonic() - t0, 1)
    return captions, meta
