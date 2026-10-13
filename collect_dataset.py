"""Build a deduplicated 1024-square Fang floor-plan archive; resume safely."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin
import zipfile
from PIL import Image, ImageOps
from crawl_floorplans import Page, fetch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
parser.add_argument('--target', type=int, default=200)
parser.add_argument('--output', default='/workspace/floorplan_dataset')
args = parser.parse_args()
if not 200 <= args.target <= 500:
    parser.error('target must be between 200 and 500')
out = Path(args.output)
(out/'images').mkdir(parents=True, exist_ok=True)
(out/'originals').mkdir(exist_ok=True)
state = out/'manifest.json'
records = json.loads(state.read_text()) if state.exists() else []
known = {r['pixel_sha256'] for r in records}
done = {r['detail_url'] for r in records}
lock = threading.Lock()
last = 0.
errors = []

def request(url):
    global last
    with lock:
        time.sleep(max(0, last + 1.5 - time.monotonic()))
        last = time.monotonic()
    return fetch(url, 0)

def page(url):
    p = Page(url)
    p.feed(request(url).decode('utf-8', errors='replace'))
    return p

def original(item):
    p = page(item['detail_url'])
    if not p.originals:
        raise RuntimeError('No published original; possible verification page')
    url = p.originals[0]
    if url.startswith('http://loupanw.soufunimg.com/'):
        url = 'https://' + url[7:]
    data = request(url)
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert('RGB')
        im.load()
        dimensions = im.size
        digest = hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()
        square = ImageOps.pad(im, (1024,1024), method=Image.Resampling.LANCZOS,
                              color='white', centering=(.5,.5))
    return item, url, data, square, dimensions, digest

def save(result):
    item,url,data,square,dimensions,digest = result
    if digest in known:
        return
    known.add(digest)
    num=len(records)+1
    file=f'images/{num:04d}.jpg'
    square.save(out/file, quality=95)
    raw=f'originals/{num:04d}.'+('png' if data.startswith(b'\x89PNG') else 'jpg')
    (out/raw).write_bytes(data)
    records.append({**item,'original_url':url,'original_file':raw,'file':file,
                    'original_dimensions':dimensions,'dimensions':[1024,1024],
                    'pixel_sha256':digest})
    temp=state.with_suffix('.tmp')
    temp.write_text(json.dumps(records,ensure_ascii=False,indent=2))
    temp.replace(state)
    print(f'Saved {len(records)}/{args.target}: {item["description"]}',flush=True)

projects=[]
current=args.url
list_seen=set()
blocked_streak=0
with ThreadPoolExecutor(max_workers=3) as pool:
    while current and len(records)<args.target and len(list_seen)<29:
        if current in list_seen:
            break
        list_seen.add(current)
        try:
            p=page(current)
        except Exception as e:
            errors.append({'url':current,'error':str(e)})
            break
        links=[u for u in p.links if re.fullmatch(r'https://newhouse\.fang\.com/loupan/\d+\.htm',u)]
        if re.fullmatch(r'https://newhouse\.fang\.com/loupan/\d+\.htm',current):
            links.insert(0,current)
        print(f'List {current}: {len(set(links))} projects',flush=True)
        for url in dict.fromkeys(links):
            if url in projects:
                continue
            projects.append(url)
            try:
                project=p if url==current else page(url)
                blocked_streak=0
                items=list({i['detail_url']:i for i in project.images if i['detail_url'] not in done}.values())
                # Bounded batch; no requests remain running after reaching the target.
                items=items[:args.target-len(records)]
                futures=[pool.submit(original,i) for i in items]
                for item,future in zip(items,futures):
                    done.add(item['detail_url'])
                    try:
                        save(future.result())
                    except Exception as e:
                        errors.append({'url':item['detail_url'],'error':str(e)})
                        print('Failed:',item['detail_url'],str(e),flush=True)
            except Exception as e:
                errors.append({'url':url,'error':str(e)})
                print('Failed project:',url,str(e),flush=True)
                blocked_streak += 1
                if blocked_streak >= 3:
                    print('Stopping after three consecutive page failures; resume after access is restored.',flush=True)
                    current=None
                    break
            if len(records)>=args.target:
                break
        if blocked_streak >= 3:
            break
        next_pages=[u for u in p.links if re.fullmatch(r'https://newhouse\.fang\.com/house/s/b9\d+/',u) and u not in list_seen]
        current=min(next_pages,key=lambda u:int(re.search(r'b9(\d+)',u)[1])) if next_pages else None

(out/'errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
(out/'README.txt').write_text(f'{len(records)} unique floor plans. images/: 1024x1024 JPEG, aspect ratio preserved with white padding. originals/: downloaded source files. manifest.json: source URLs and original dimensions. No CAPTCHA or login bypass. Source image copyright remains with rights holders.\n')
for r in records:
    with Image.open(out/r['file']) as im:
        im.load()
        assert im.size==(1024,1024)
archive=out.with_suffix('.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(out.rglob('*')):
        if f.is_file():
            z.write(f, f.relative_to(out.parent))
print(f'COMPLETE {len(records)} images; {archive}; failures {len(errors)}',flush=True)
raise SystemExit(0 if len(records)>=args.target else 1)
