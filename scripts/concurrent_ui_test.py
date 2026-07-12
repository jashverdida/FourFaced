"""Fire two clips at the server simultaneously; the second must queue."""
import json
import threading
import time

import requests

BASE = "http://localhost:5000"
CLIPS = [
    ("A", "https://storage.googleapis.com/amd-hackathon-clips/1860079-uhd_2560_1440_25fps.mp4"),
    ("B", "https://storage.googleapis.com/amd-hackathon-clips/3044693-uhd_3840_2160_24fps.mp4"),
]
t0 = time.monotonic()


def run(tag, url):
    job = requests.post(f"{BASE}/api/upload", data={"example_url": url}).json()["job_id"]
    with requests.get(f"{BASE}/api/run/{job}", stream=True, timeout=300) as resp:
        event = None
        for line in resp.iter_lines(decode_unicode=True):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                name = data.get("name", "done")
                print(f"[{time.monotonic() - t0:6.1f}s] clip {tag}: {name}")
                if event == "done":
                    print(f"         clip {tag} done: total_s={data.get('total_s')} "
                          f"budget_s={data.get('budget_s')} "
                          f"template_styles={data.get('template_styles')} "
                          f"grounded={'yes' if data.get('facts') else 'NO'} "
                          f"error={data.get('error')}")


threads = [threading.Thread(target=run, args=c) for c in CLIPS]
for th in threads:
    th.start()
for th in threads:
    th.join()
