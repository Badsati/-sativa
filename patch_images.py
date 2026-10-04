"""
patch_images.py — back-fill image data into already-scraped question JSON files.

For each question that has a URL and no "images" field yet, re-fetches the page,
finds <img> tags inside the question body, downloads each image, saves it under
data/<provider>/images/<exam>/<hash>.<ext>, and writes the relative path back
into the question's "images" list.

Usage:
    python patch_images.py                        # patch all providers
    python patch_images.py --provider ibm         # single provider
    python patch_images.py --provider ibm --exam c1000-012  # single exam
    python patch_images.py --dry-run              # report counts, no writes
    python patch_images.py --commit               # git commit after each file
"""

import argparse
import hashlib
import json
import mimetypes
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from examtopics.settings import REQUEST_HEADERS

DATA_DIR = Path("data")
DELAY = 2.0  # seconds between page fetches


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    return s


SESSION = _make_session()


def _fetch_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        print(f"    [WARN] fetch failed {url}: {exc}")
        return None


def _img_ext(url: str, content_type: str = "") -> str:
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
        return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ".img"


def _resolve_url(src: str, page_url: str) -> str:
    if src.startswith("data:"):
        return ""
    return urllib.parse.urljoin(page_url, src)


def extract_question_images(html: str, page_url: str) -> list:
    """Return absolute image URLs found inside the question body."""
    soup = BeautifulSoup(html, "html.parser")
    body = (
        soup.select_one(".question-body")
        or soup.select_one(".card-text")
        or soup.select_one(".question-text")
    )
    if not body:
        return []
    srcs = []
    for img in body.find_all("img"):
        src = img.get("src", "").strip()
        if not src:
            continue
        resolved = _resolve_url(src, page_url)
        if resolved:
            srcs.append(resolved)
    return srcs


def download_image(url: str, dest_dir: Path, index: int) -> Optional[str]:
    """Download image to dest_dir as img_<index>.<ext>. Returns path string or None."""
    data = _fetch_bytes(url)
    if data is None:
        return None

    try:
        head = SESSION.head(url, timeout=10)
        ct = head.headers.get("Content-Type", "")
    except Exception:
        ct = ""

    ext = _img_ext(url, ct)
    name = f"img_{index}{ext}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(data)
    return str(dest)


def _question_slug(q: dict, idx: int) -> str:
    """Derive a short filesystem-safe slug from the question number field."""
    raw = q.get("question_no", "")
    # e.g. "Exam C1000-012 topic 1 question 23 discussion" -> "q023"
    m = re.search(r"question\s+(\d+)", raw, re.I)
    if m:
        return f"q{int(m.group(1)):03d}"
    return f"q{idx:03d}"


def patch_file(json_path: Path, dry_run: bool) -> int:
    """Patch one JSON file in-place. Returns count of questions with images found."""
    questions = json.loads(json_path.read_text())
    provider = json_path.parent.name
    exam = json_path.stem
    base_images_dir = DATA_DIR / provider / "images" / exam

    patched = 0
    changed = False

    for idx, q in enumerate(questions, start=1):
        if "images" in q:
            continue  # already patched

        url = q.get("url", "")
        if not url:
            q["images"] = []
            continue

        raw = _fetch_bytes(url)
        time.sleep(DELAY)
        if raw is None:
            q["images"] = []
            continue

        html = raw.decode("utf-8", errors="replace")
        img_urls = extract_question_images(html, url)

        if not img_urls:
            q["images"] = []
            continue

        print(f"    [{exam}] {q.get('question_no', url)}: {len(img_urls)} image(s)")

        if dry_run:
            q["images"] = img_urls
            patched += 1
            continue

        # Each question gets its own subfolder: images/<exam>/q001/img_1.png
        q_dir = base_images_dir / _question_slug(q, idx)
        saved = []
        for i, img_url in enumerate(img_urls, start=1):
            path = download_image(img_url, q_dir, i)
            saved.append(path if path else img_url)
            print(f"      saved → {saved[-1]}")

        q["images"] = saved
        patched += 1
        changed = True

    if changed and not dry_run:
        json_path.write_text(json.dumps(questions, indent=2, ensure_ascii=False))
        print(f"  Updated {json_path}")

    return patched


def git_commit(json_path: Path) -> None:
    provider = json_path.parent.name
    exam = json_path.stem
    images_dir = DATA_DIR / provider / "images" / exam
    try:
        subprocess.run(["git", "add", str(json_path), str(images_dir)], check=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"images: {provider}/{exam}"],
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"  Committed and pushed images for {provider}/{exam}")
    except Exception as e:
        print(f"  [WARN] Git commit failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Back-fill images into scraped question JSON files")
    parser.add_argument("--provider", default=None, help="Limit to one provider slug (e.g. amazon)")
    parser.add_argument("--exam", default=None, help="Limit to one exam slug (e.g. aws-clf-c02)")
    parser.add_argument("--dry-run", action="store_true", help="Report image counts without downloading or writing")
    parser.add_argument("--commit", action="store_true", help="Git commit and push after each file")
    args = parser.parse_args()

    json_files = sorted(DATA_DIR.glob("**/*.json"))
    if args.provider:
        json_files = [f for f in json_files if f.parent.name == args.provider]
    if args.exam:
        json_files = [f for f in json_files if f.stem == args.exam]

    if not json_files:
        print("No JSON files found matching the given filters.")
        return

    total_patched = 0
    for json_path in json_files:
        print(f"[{json_path.parent.name}/{json_path.stem}]")
        count = patch_file(json_path, dry_run=args.dry_run)
        total_patched += count
        if count and args.commit and not args.dry_run:
            git_commit(json_path)

    print(f"\nDone. {total_patched} question(s) with images patched across {len(json_files)} file(s).")


if __name__ == "__main__":
    main()
