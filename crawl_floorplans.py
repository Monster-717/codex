#!/usr/bin/env python3
"""Collect floor-plan thumbnails from public Fang project pages (Python + curl)."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin, urlsplit


class Page(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url, self.anchor = url, ""
        self.links, self.images, self.originals = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("data-imgsource"):
            self.originals.append(urljoin(self.url, a["data-imgsource"]))
        if tag == "a":
            self.anchor = urljoin(self.url, a.get("href", ""))
            self.links.append(self.anchor)
        if tag == "img" and re.search(r"/photo/d_house_\d+\.htm", self.anchor):
            src = a.get("data-src") or a.get("src")
            if src:
                self.images.append({"page_url": self.url, "detail_url": self.anchor,
                                    "image_url": urljoin(self.url, src),
                                    "description": a.get("alt", ""),
                                    "resolution": "published thumbnail"})

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = ""


def fetch(url, delay):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("Only HTTP(S) URLs are supported")
    time.sleep(delay)
    with tempfile.TemporaryDirectory() as temp:
        body = Path(temp) / "body"
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--max-time", "30",
             "--max-filesize", "20971520", "--output", str(body),
             "--write-out", "%{http_code}", url], capture_output=True, text=True)
        if result.returncode or result.stdout != "200":
            raise RuntimeError(f"HTTP {result.stdout or 'unknown'}: {result.stderr.strip()}")
        return body.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="floorplans")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--original", action="store_true",
                        help="Read each floor-plan detail page and download its published original")
    args = parser.parse_args()
    if args.max_pages < 1 or args.delay < 0:
        parser.error("max-pages must be positive; delay must be nonnegative")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    queue, seen, records, errors = [args.url], set(), [], []
    image_urls = set()
    while queue and len(seen) < args.max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        print(f"Page: {url}", flush=True)
        try:
            page = Page(url)
            page.feed(fetch(url, args.delay).decode("utf-8", errors="replace"))
            for link in page.links:
                if (urlsplit(link).hostname == urlsplit(url).hostname
                        and re.search(r"/loupan/\d+\.htm$", link)
                        and link not in seen and link not in queue):
                    queue.append(link)
            for item in page.images:
                if item["image_url"] in image_urls:
                    continue
                image_urls.add(item["image_url"])
                records.append(item)
                try:
                    if args.original:
                        detail = Page(item["detail_url"])
                        detail.feed(fetch(item["detail_url"], args.delay).decode("utf-8", errors="replace"))
                        if not detail.originals:
                            raise RuntimeError("Detail page provides no original image link")
                        item["thumbnail_url"] = item["image_url"]
                        original = detail.originals[0]
                        # Fang also publishes the same source URL over HTTPS in JSON-LD.
                        if original.startswith("http://loupanw.soufunimg.com/"):
                            original = "https://" + original[len("http://"):]
                        item.update(image_url=original, resolution="published original")
                    data = fetch(item["image_url"], args.delay)
                    ext = (".jpg" if data.startswith(b"\xff\xd8\xff") else
                           ".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                           ".webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
                    if ext is None:
                        raise RuntimeError("Response is not a supported image")
                    name = hashlib.sha256(data).hexdigest() + ext
                    path = out / name
                    if not path.exists():
                        path.write_bytes(data)
                    item.update(status="downloaded", file=name)
                except Exception as error:
                    item.update(status="failed", error=str(error))
        except Exception as error:
            errors.append({"url": url, "error": str(error)})
    manifest = {"start_url": args.url, "pages_visited": len(seen),
                "images": records, "page_errors": errors}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    downloaded = sum(x["status"] == "downloaded" for x in records)
    print(f"Found {len(records)}; downloaded {downloaded}; page errors {len(errors)}")
    return 0 if downloaded and not errors and downloaded == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
