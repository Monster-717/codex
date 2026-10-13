"""Resume collection with bounded exponential backoff; never count duplicates."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--url', default='https://newhouse.fang.com/house/s/')
p.add_argument('--output', default='/workspace/floorplan_dataset')
p.add_argument('--target', type=int, default=200)
p.add_argument('--attempts', type=int, default=3)
p.add_argument('--backoff', type=int, default=60)
a = p.parse_args()
if a.attempts < 1 or a.backoff < 1 or not 200 <= a.target <= 500:
    p.error('Use positive attempts/backoff and target between 200 and 500')
script = Path(__file__).with_name('collect_dataset.py')
for attempt in range(a.attempts):
    if attempt:
        delay = min(a.backoff * 2 ** (attempt - 1), 900)
        print(f'Waiting {delay}s before retry', flush=True)
        time.sleep(delay)
    result = subprocess.run([sys.executable, str(script), '--url', a.url,
                             '--output', a.output, '--target', str(a.target)])
    manifest = Path(a.output) / 'manifest.json'
    count = len(json.loads(manifest.read_text())) if manifest.exists() else 0
    print(f'Attempt {attempt+1}: {count}/{a.target} images retained', flush=True)
    if result.returncode == 0 and count >= a.target:
        raise SystemExit(0)
print('Target unmet after retries; retained images and ZIP remain available.', flush=True)
raise SystemExit(1)
