"""Collect Fang originals in a persistent browser; humans complete verification."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import zipfile
from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright, Error
from crawl_floorplans import Page


def write_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True)
    p.add_argument('--target', type=int, default=200)
    p.add_argument('--output', default='floorplan_dataset')
    p.add_argument('--profile', default='.fang-browser-profile')
    p.add_argument('--delay', type=float, default=5)
    p.add_argument('--headless', action='store_true', help='For diagnostics; cannot manually verify')
    p.add_argument('--executable', help='Optional Chromium executable path')
    args = p.parse_args()
    if not 1 <= args.target <= 500 or args.delay < 0 or urlsplit(args.url).scheme not in ('http','https'):
        p.error('Use an HTTP(S) URL, target 1–500 and nonnegative delay')
    out = Path(args.output).resolve()
    profile = Path(args.profile).resolve()
    if profile == out or out in profile.parents:
        p.error('Keep the private browser profile outside the dataset output')
    for folder in ('images','originals'):
        (out/folder).mkdir(parents=True, exist_ok=True)
    manifest = out/'manifest.json'
    records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    for r in records:
        if not (out/r['file']).is_file() or not (out/r['original_file']).is_file():
            raise RuntimeError('Existing manifest references missing files; restore them before resuming')
    done = {r['detail_url'] for r in records}
    hashes = {r['pixel_sha256'] for r in records}
    errors = []
    host = urlsplit(args.url).hostname
    queue, visited = [args.url], set()
    interrupted = False
    with sync_playwright() as pw:
        executable = args.executable or shutil.which('chromium')
        options = {'headless':args.headless, 'viewport':{'width':1280,'height':900}}
        if executable:
            options['executable_path'] = executable
        context = pw.chromium.launch_persistent_context(str(profile), **options)
        tab = context.pages[0] if context.pages else context.new_page()

        def navigate(url):
            for attempt in range(3):
                time.sleep(args.delay)
                try:
                    response = tab.goto(url, wait_until='domcontentloaded', timeout=45000)
                    tab.wait_for_timeout(1500)
                except Error:
                    if attempt == 2:
                        raise
                    time.sleep(5 * 2**attempt)
                    continue
                redirected = urlsplit(tab.url).hostname != urlsplit(url).hostname
                text = tab.locator('body').inner_text(timeout=10000)
                challenge = redirected or any(s in text for s in ('请完成安全验证','访问验证','请拖动滑块','请输入验证码'))
                if challenge:
                    if args.headless:
                        raise RuntimeError('Verification required: rerun with a visible browser')
                    print(f'\n请在打开的浏览器中手动完成验证。目标页：{url}', flush=True)
                    input('验证完成后，在此终端按回车继续；Ctrl+C 可保存并退出：')
                    return navigate(url)
                if response is not None and response.status >= 400:
                    if response.status >= 500 and attempt < 2:
                        time.sleep(5 * 2**attempt)
                        continue
                    raise RuntimeError(f'HTTP {response.status}')
                result = Page(url)
                result.feed(tab.content())
                return result
            raise RuntimeError('Navigation retries exhausted')

        try:
            while queue and len(records) < args.target:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                print('Page:',url,flush=True)
                page = navigate(url)
                for link in page.links:
                    path = urlsplit(link).path
                    if (urlsplit(link).hostname == host and
                        (re.fullmatch(r'/loupan/\d+\.htm',path) or
                         re.fullmatch(r'/house/s/b9\d+/',path)) and
                        link not in visited and link not in queue):
                        queue.append(link)
                for item in {i['detail_url']:i for i in page.images}.values():
                    if len(records) >= args.target:
                        break
                    if item['detail_url'] in done:
                        continue
                    try:
                        detail = navigate(item['detail_url'])
                        if not detail.originals:
                            raise RuntimeError('No original link on detail page')
                        image_url = detail.originals[0]
                        if image_url.startswith('http://loupanw.soufunimg.com/'):
                            image_url = 'https://' + image_url[7:]
                        time.sleep(args.delay)
                        response = context.request.get(image_url, timeout=45000,
                                                       headers={'Referer':item['detail_url']})
                        if not response.ok:
                            raise RuntimeError(f'Image HTTP {response.status}')
                        data = response.body()
                        response.dispose()
                        with Image.open(io.BytesIO(data)) as image:
                            fmt = image.format
                            image = ImageOps.exif_transpose(image).convert('RGB')
                            image.load()
                            size = image.size
                            digest = hashlib.sha256(str(size).encode()+image.tobytes()).hexdigest()
                            square = ImageOps.pad(image,(1024,1024),color='white',method=Image.Resampling.LANCZOS)
                        done.add(item['detail_url'])
                        if digest in hashes:
                            continue
                        hashes.add(digest)
                        num = len(records)+1
                        file = f'images/{num:04d}.jpg'
                        raw = f'originals/{num:04d}.'+{'PNG':'png','JPEG':'jpg','WEBP':'webp'}.get(fmt,'img')
                        square.save(out/file,quality=95)
                        (out/raw).write_bytes(data)
                        records.append({**item,'original_url':image_url,'original_file':raw,'file':file,
                                        'original_dimensions':size,'dimensions':[1024,1024],'pixel_sha256':digest})
                        write_json(manifest,records)
                        print(f'Saved {len(records)}/{args.target}',flush=True)
                    except (Error, RuntimeError, OSError) as e:
                        errors.append({'url':item['detail_url'],'error':str(e)})
                        print('Failed:',str(e),flush=True)
        except KeyboardInterrupt:
            interrupted = True
            print('Interrupted; preserving downloaded images.',flush=True)
        except (Error, RuntimeError, EOFError) as e:
            errors.append({'url':tab.url,'error':str(e)})
            print('Stopped:',str(e),flush=True)
        finally:
            context.close()
    write_json(manifest,records)
    write_json(out/'browser_errors.json',errors)
    (out/'README.txt').write_text(f'{len(records)} unique floor plans. images/: aspect-preserving 1024x1024 JPEGs with white padding. originals/: source files. manifest.json: sources. Browser profile and credentials are excluded.\n', encoding="utf-8")
    for r in records:
        with Image.open(out/r['file']) as image:
            image.load()
            if image.size != (1024,1024):
                raise RuntimeError('Invalid output image size')
    archive = out.with_suffix('.zip')
    temp_archive = archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temp_archive,'w',zipfile.ZIP_DEFLATED) as z:
        for file in out.rglob('*'):
            if file.is_file():
                z.write(file,file.relative_to(out.parent))
    temp_archive.replace(archive)
    print(f'{len(records)}/{args.target}; ZIP: {archive}',flush=True)
    return 0 if len(records)>=args.target else 130 if interrupted else 1


if __name__ == '__main__':
    raise SystemExit(main())
