# FourFaced

*One clip. Four faces. Every caption backed by what's actually on screen.*

FourFaced is Team VERPTO's submission for **Track 2: Video Captioning** of the
**AMD Developer Hackathon: ACT II**. It is a containerized batch-processing
engine: it reads a list of video tasks from `/input/tasks.json`, runs a
two-stage, all-Gemma vision-and-language pipeline for each clip, and writes
the final captions to `/output/results.json` before exiting.

The pipeline:

1. **Grounding** — sampled video frames go to a Gemma 4 vision model, which
   returns a plain, factual description of what is actually on screen.
2. **Styling** — that grounded description goes back to Gemma 4, which drafts
   the captions in every requested style (`formal`, `sarcastic`,
   `humorous_tech`, `humorous_non_tech`) and checks them against the grounding
   facts to remove hallucinated details.

Gemma 4 runs the *entire* pipeline — vision grounding and styling — never split
across model providers.

> **Important:** This container is a **batch processor**, not a web server. It
> does not expose any port and does not run a persistent HTTP service. Judges
> should run it with volume mounts and an API key, then inspect the output
> file.

## Input / output contract

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

## Pull the official image

```sh
docker pull eijay/fourfaced:final-submission
```

## Run the container

Because this is a batch processor, **no port mapping (`-p`) is required**.
Mount your input directory, an output directory, and pass a valid
`GEMINI_API_KEY`:

```sh
docker run --rm \
  --platform linux/amd64 \
  -v /path/to/input:/input:ro \
  -v /path/to/output:/output \
  -e GEMINI_API_KEY=<your-key> \
  eijay/fourfaced:final-submission
```

`/path/to/input` must contain a `tasks.json`. Results appear at
`/path/to/output/results.json`.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google AI Studio key (Gemini API serves Gemma 4) | *(required)* |
| `GROUND_MODEL` | Vision-grounding model | `gemma-4-31b-it` |
| `STYLE_MODEL` | Caption-styling model | `gemma-4-31b-it` |
| `PER_CLIP_BUDGET` | Per-clip wall-clock budget (seconds) | `27` |

## Demo application

For a visual UI demonstration of the same pipeline, visit our live Vercel app:

**[https://four-faced-demo.vercel.app/](https://four-faced-demo.vercel.app/)**

The demo page is a static presentation of the FourFaced interface. The
judging container itself is the batch processor described above.

## Build locally

```sh
docker buildx build --platform linux/amd64 --tag fourfaced:latest .
```

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

## Known limitations

- **Discrete fast events on longer clips can fall between sampled frames.**
  Frame sampling follows the competition guide's cadence (roughly one frame
  per 4-5 seconds, clamped to 6-15 frames), biased toward the denser end of
  that range for longer clips. Even so, on a ~37s test clip containing a
  scored goal and a referee card, grounding still missed the card entirely
  and described the goal ambiguously. This is an inherent tradeoff of sparse
  frame sampling on longer, fast-action clips (e.g. sports) rather than a bug;
  going denser than the guide's cadence would trade clip-budget margin for
  uncertain accuracy gains, so it hasn't been pushed further.

## Team

**VERPTO** — Jashmine "Jash" Verdida & Eijay Pepito.
