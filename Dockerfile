# FourFaced — Track 2 video captioning container.
# Always build with: docker buildx build --platform linux/amd64 ...
FROM --platform=linux/amd64 python:3.12-slim

# ffmpeg/ffprobe are used by the Phase 2 frame-sampling pipeline.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENTRYPOINT ["python", "-u", "app/main.py"]
