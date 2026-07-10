#!/usr/bin/env sh
# Build the container and run it against the official example clips,
# then validate the output. Mirrors exactly how the harness runs us.
# Usage: sh scripts/test_container.sh
set -eu

cd "$(dirname "$0")/.."

docker buildx build --platform linux/amd64 --tag fourfaced:latest --load .

rm -rf local_output && mkdir -p local_output

docker run --rm \
  --platform linux/amd64 \
  -v "$(pwd)/tests/sample_input:/input:ro" \
  -v "$(pwd)/local_output:/output" \
  -e GEMINI_API_KEY="${GEMINI_API_KEY:-}" \
  fourfaced:latest

python scripts/validate_output.py local_output/results.json tests/sample_input/tasks.json
echo "Container test passed."
