"""FourFaced results UI — local Flask server.

Runs the exact same ground-then-style Gemma pipeline as the competition
container (imported directly from app/), staged so the frontend can show
real progress instead of a fake spinner. Not part of the submission
container: kept out of the Docker build entirely (see .dockerignore).
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, APP_DIR)

import main  # noqa: E402  loads repo .env, exposes fallback_captions
import pipeline  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JOBS_ROOT = os.path.join(tempfile.gettempdir(), "fourfaced_ui_jobs")
ALL_STYLES = ["formal", "sarcastic", "humorous_tech", "humorous_non_tech"]

app = Flask(__name__, static_folder=None)
JOBS = {}

# The three official example clips, so the UI can demo without a local file.
EXAMPLES = [
    {"id": "v1", "label": "Urban autumn boulevard",
     "url": "https://storage.googleapis.com/amd-hackathon-clips/1860079-uhd_2560_1440_25fps.mp4"},
    {"id": "v2", "label": "Kitten in the garden",
     "url": "https://storage.googleapis.com/amd-hackathon-clips/13825391-uhd_3840_2160_30fps.mp4"},
    {"id": "v3", "label": "Office desk, at work",
     "url": "https://storage.googleapis.com/amd-hackathon-clips/3044693-uhd_3840_2160_24fps.mp4"},
]


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/examples")
def examples():
    return jsonify(EXAMPLES)


@app.route("/api/upload", methods=["POST"])
def upload():
    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(JOBS_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)

    example_url = request.form.get("example_url")
    if example_url:
        if not any(e["url"] == example_url for e in EXAMPLES):
            return jsonify({"error": "Unknown example clip"}), 400
        JOBS[job_id] = {"source_url": example_url, "dir": job_dir}
        return jsonify({"job_id": job_id})

    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify({"error": "No video file provided"}), 400
    dest = os.path.join(job_dir, "source.mp4")
    file.save(dest)
    JOBS[job_id] = {"source_url": pathlib.Path(dest).resolve().as_uri(), "dir": job_dir}
    return jsonify({"job_id": job_id})


def sse(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def run_pipeline_staged(source_url: str, styles: list, deadline: float):
    """Mirrors pipeline.process_task's ladder, yielding (event, payload)
    at each real stage boundary instead of running silently to completion.
    """
    with tempfile.TemporaryDirectory(prefix="fourfaced_ui_run_") as tmp:
        yield "stage", {"name": "probing", "label": "Reading the clip…"}
        video_path = os.path.join(tmp, "clip.mp4")
        pipeline.download_video(source_url, video_path, deadline)
        duration = pipeline.probe_duration(video_path)

        yield "stage", {"name": "sampling",
                        "label": f"Sampling frames from {duration:.0f}s of footage…"}
        frames = pipeline.sample_frames(video_path, duration, tmp, deadline)

        yield "stage", {"name": "grounding",
                        "label": f"Grounding {len(frames)} frames with Gemma 4…"}
        facts = pipeline.ground(frames, duration, deadline)

    yield "stage", {"name": "drafting", "label": "Drafting captions in four voices…"}
    captions = {}
    try:
        captions = pipeline.style_captions(facts, styles, deadline)
    except Exception:
        pass

    missing = pipeline.missing_styles(captions, styles)
    strict_retry = False
    if missing and deadline - time.monotonic() >= 10:
        strict_retry = True
        yield "stage", {"name": "retry", "label": f"Retrying {', '.join(missing)}…"}
        try:
            retry = pipeline.style_captions(facts, styles, deadline, strict=True)
            for s in pipeline.missing_styles(captions, styles):
                if s not in pipeline.missing_styles(retry, [s]):
                    captions[s] = retry[s]
        except Exception:
            pass

    still_missing = pipeline.missing_styles(captions, styles)
    if still_missing:
        yield "stage", {"name": "fallback",
                        "label": f"Filling {', '.join(still_missing)} from the grounding facts…"}
        fills = pipeline.template_captions(facts, still_missing)
        for s in still_missing:
            captions[s] = fills[s]
    captions = {s: captions[s].strip() for s in styles}

    style_thinking = False
    if not still_missing and deadline - time.monotonic() >= pipeline.STYLE_REFINE_MIN_BUDGET:
        yield "stage", {"name": "refining",
                        "label": "Gemma is checking drafts against the facts…"}
        try:
            captions = pipeline.refine_captions(facts, captions, styles, deadline)
            style_thinking = True
        except Exception:
            pass

    yield "done", {
        "facts": facts,
        "captions": captions,
        "duration_s": round(duration, 1),
        "frame_count": len(frames),
        "style_thinking": style_thinking,
        "template_styles": still_missing,
        "strict_retry": strict_retry,
    }


@app.route("/api/run/<job_id>")
def run(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404

    styles = [s for s in request.args.get("styles", "").split(",") if s in ALL_STYLES]
    styles = styles or ALL_STYLES

    def stream():
        deadline = time.monotonic() + pipeline.PER_CLIP_BUDGET
        t0 = time.monotonic()
        try:
            for event, payload in run_pipeline_staged(job["source_url"], styles, deadline):
                if event == "done":
                    payload["total_s"] = round(time.monotonic() - t0, 1)
                yield sse(event, payload)
        except Exception as e:
            # Same last-resort safety net as the competition container: a
            # grounding-stage failure still returns real, if generic, captions.
            yield sse("stage", {"name": "fallback", "label": "Falling back to generic captions…"})
            yield sse("done", {
                "facts": None,
                "captions": main.fallback_captions(styles),
                "duration_s": None,
                "frame_count": 0,
                "style_thinking": False,
                "template_styles": styles,
                "strict_retry": False,
                "total_s": round(time.monotonic() - t0, 1),
                "error": str(e),
            })
        finally:
            shutil.rmtree(job["dir"], ignore_errors=True)
            JOBS.pop(job_id, None)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    os.makedirs(JOBS_ROOT, exist_ok=True)
    print(f"FourFaced UI: http://localhost:5000  (ground={pipeline.GROUND_MODEL} style={pipeline.STYLE_MODEL})")
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
