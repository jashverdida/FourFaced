# FourFaced

*One clip. Four faces. Every caption backed by what's actually on screen.*

FourFaced is Team VERPTO's submission for **Track 2: Video Captioning** of the
**AMD Developer Hackathon: ACT II**. Given a batch of video clips, it generates
a caption for each clip in each requested style (`formal`, `sarcastic`,
`humorous_tech`, `humorous_non_tech`) using a two-stage, all-Gemma pipeline:

1. **Grounding** — sampled video frames go to a Fireworks-hosted Gemma 4 vision
   model, which returns a plain, factual description of what is actually on
   screen.
2. **Styling** — that grounded description goes back to Gemma 4, which drafts
   the captions in every requested style, checks its own drafts against the
   grounding facts to remove hallucinated details, and returns the final
   captions.

Gemma 4 runs the *entire* pipeline — vision grounding and styling — never split
across model providers.

> **Status: Phase 2 (real pipeline).** The full Gemma 4 pipeline is live:
> frames are sampled with ffmpeg, grounded and styled by `gemma-4-31b-it` on
> the Gemini API, with per-clip time budgets and safe fallbacks throughout.

## Contract

The container reads `/input/tasks.json`:

```json
[
  {
    "task_id": "v1",
    "video_url": "https://storage.example.com/clips/clip1.mp4",
    "styles": ["formal", "sarcastic", "humorous_tech", "humorous_non_tech"]
  }
]
```

and writes `/output/results.json` before exiting 0:

```json
[
  {
    "task_id": "v1",
    "captions": {
      "formal": "...",
      "sarcastic": "...",
      "humorous_tech": "...",
      "humorous_non_tech": "..."
    }
  }
]
```

## Build

```sh
docker buildx build --platform linux/amd64 --tag fourfaced:latest .
```

(Add `--push` and a registry tag when publishing for submission.)

## Run

```sh
docker run --rm \
  -v /path/to/input:/input:ro \
  -v /path/to/output:/output \
  -e GEMINI_API_KEY=your-key-here \
  fourfaced:latest
```

`/path/to/input` must contain a `tasks.json`; results appear at
`/path/to/output/results.json`.

### Environment variables

Copy `.env.example` to `.env` for local use. Never commit a real key.

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google AI Studio key (Gemini API serves Gemma 4) | *(required)* |
| `GROUND_MODEL` | Vision-grounding model | `gemma-4-31b-it` |
| `STYLE_MODEL` | Caption-styling model | `gemma-4-31b-it` |
| `PER_CLIP_BUDGET` | Per-clip wall-clock budget (seconds) | `27` |

## Test locally

Against the three official example clips (in `tests/sample_input/tasks.json`):

```sh
# Full container test (requires Docker):
sh scripts/test_container.sh

# Or run the pipeline directly with Python, no Docker (Windows):
powershell -File scripts/run_local.ps1
```

Both run the pipeline and then validate the output against the contract with
`scripts/validate_output.py`.

## Sample input/output pair

Input (`tests/sample_input/tasks.json`, abbreviated to one task):

```json
[
  {
    "task_id": "v2",
    "video_url": "https://storage.googleapis.com/amd-hackathon-clips/13825391-uhd_3840_2160_30fps.mp4",
    "styles": ["formal", "sarcastic", "humorous_tech", "humorous_non_tech"]
  }
]
```

Output (`results.json`) — real pipeline output:

```json
[
  {
    "task_id": "v2",
    "captions": {
      "formal": "A young orange kitten walks through a wooded area toward a stationary camera.",
      "sarcastic": "A small orange cat walks forward. Truly a cinematic masterpiece.",
      "humorous_tech": "New kitten.exe has been deployed to the forest environment and is currently booting up.",
      "humorous_non_tech": "This tiny orange fluff ball is walking with the confidence of someone who actually knows where they are going."
    }
  }
]
```

Alongside `results.json`, the container writes `fourfaced_debug.json` with the
grounding facts, stage timings, and fallback flags per clip — the evidence
behind every caption (the grading harness only reads `results.json`).

## Team

**VERPTO** — Jashmine "Jash" Verdida & Eijay Pepito.
