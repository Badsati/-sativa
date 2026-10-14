"""
fetch_images.py — re-scan a provider's discussion pages and download images
for questions that reference exhibits, figures, diagrams, screenshots, etc.

Since most existing JSON files lack a stored URL, this script re-discovers
question URLs via the discussion listing pages, then fetches each one,
downloads images where the question references visual content, and matches
the result back into the existing JSON file by question text or
topic/question number.

Images are stored under:
    images/<provider>/<exam>/t<topic>_q<num>/img_<i>.<ext>

The matched JSON entry gains an "images" list of those relative paths,
and a "url" field if it was missing.

Usage:
    python fetch_images.py --provider ibm
    python fetch_images.py --provider ibm --exam c1000-012
    python fetch_images.py --provider ibm --dry-run
    python fetch_images.py --provider ibm --commit
    python fetch_images.py --provider ibm --workers 2
"""

import argparse
import json
import mimetypes
import random
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from examtopics.cache import HtmlCache
from examtopics.fast_scanner import FastDiscussionScanner
from examtopics.http_client import HttpFetcher
from examtopics.matching import build_page_numbers, extract_topic_question, normalize_provider
from examtopics.question_parser import parse_question_page
from examtopics.settings import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_DELAY_RANGE,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_MS,
    REQUEST_HEADERS,
)

DATA_DIR = Path("data")
IMAGES_DIR = Path("images")

EXHIBIT_RE = re.compile(
    r"\b(exhibit|figure|fig\.|diagram|screenshot|illustration|"
    r"refer to the following|shown below|shown in the|the image|the graphic)\b",
    re.I,
)


# ── URL / slug helpers ────────────────────────────────────────────────────────

def _topic_qnum_from_url(url: str) -> Tuple[int, int]:
    m = re.search(r"topic-(\d+)-question-(\d+)", url, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _question_dir_name(url: str) -> str:
    topic, qnum = _topic_qnum_from_url(url)
    if topic or qnum:
        return f"t{topic:02d}_q{qnum:03d}"
    slug = re.sub(r"[^a-z0-9]+", "_", url.lower())[-40:]
    return slug


# ── Exhibit detection ─────────────────────────────────────────────────────────

def _references_image(question_text: str, options: Dict[str, str]) -> bool:
    combined = question_text + " " + " ".join(options.values())
    return bool(EXHIBIT_RE.search(combined))


# ── Image download helpers ────────────────────────────────────────────────────

_img_session: Optional[requests.Session] = None


def _get_img_session() -> requests.Session:
    global _img_session
    if _img_session is None:
        s = requests.Session()
        s.headers.update(REQUEST_HEADERS)
        _img_session = s
    return _img_session


def _fetch_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        r = _get_img_session().get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        tqdm.write(f"    [WARN] image fetch failed {url}: {exc}")
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
    if not src or src.startswith("data:"):
        return ""
    return urllib.parse.urljoin(page_url, src)


def extract_question_images(html: str, page_url: str) -> List[str]:
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
        resolved = _resolve_url(src, page_url)
        if resolved:
            srcs.append(resolved)
    return srcs


def download_images(img_urls: List[str], dest_dir: Path) -> List[str]:
    """Download all image URLs into dest_dir. Returns list of saved relative paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, url in enumerate(img_urls, start=1):
        data = _fetch_bytes(url)
        if data is None:
            saved.append(url)  # keep the remote URL as fallback
            continue
        try:
            head = _get_img_session().head(url, timeout=10)
            ct = head.headers.get("Content-Type", "")
        except Exception:
            ct = ""
        ext = _img_ext(url, ct)
        path = dest_dir / f"img_{i}{ext}"
        path.write_bytes(data)
        saved.append(str(path))
    return saved


# ── JSON matching ─────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _match_entry(questions: List[dict], parsed_q: dict, url: str) -> Optional[int]:
    """Return index of the best-matching question in `questions`, or None."""
    needle_text = _norm(parsed_q.get("question", ""))

    # 1. Exact normalised text match
    for i, q in enumerate(questions):
        if _norm(q.get("question", "")) == needle_text:
            return i

    # 2. topic+question number from URL vs question_no field
    topic, qnum = _topic_qnum_from_url(url)
    if topic or qnum:
        pat = re.compile(
            rf"topic\s+{topic}\s+question\s+{qnum}\b", re.I
        )
        for i, q in enumerate(questions):
            if pat.search(q.get("question_no", "")):
                return i

    return None


# ── Per-exam processing ───────────────────────────────────────────────────────

def process_exam(
    provider: str,
    exam: str,
    urls: List[str],
    fetcher: HttpFetcher,
    dry_run: bool,
) -> int:
    """
    For each URL in `urls`, fetch the question page, check for exhibit refs,
    download images, and update the exam's JSON file.
    Returns (images_found, json_changed).
    """
    json_path = DATA_DIR / provider / f"{exam}.json"
    if not json_path.exists():
        tqdm.write(f"  [SKIP] no JSON file at {json_path}")
        return 0

    questions = json.loads(json_path.read_text())
    base_img_dir = IMAGES_DIR / provider / exam

    patched = 0
    changed = False

    for url in tqdm(urls, desc=f"  {exam}", unit="q", leave=False):
        # Find matching entry first (if it already has images, skip)
        # We'll do the match after fetching, but we can skip URLs that
        # have already been processed by checking if all questions are done.
        # (We check post-fetch to avoid an extra scan pass.)

        try:
            html = fetcher.fetch_html(url)
        except Exception as exc:
            tqdm.write(f"    [WARN] fetch failed {url}: {exc}")
            time.sleep(random.uniform(2.0, 4.0))
            continue

        parsed = parse_question_page(html, url=url)

        # Find matching entry in JSON
        idx = _match_entry(questions, parsed, url)
        if idx is None:
            tqdm.write(f"    [WARN] no match in JSON for {url}")
            time.sleep(random.uniform(*DEFAULT_DELAY_RANGE))
            continue

        q = questions[idx]

        # Always back-fill url if missing
        if not q.get("url"):
            q["url"] = url
            changed = True

        # Skip if images already recorded
        if "images" in q:
            time.sleep(random.uniform(1.0, 2.0))
            continue

        # Check exhibit reference
        if not _references_image(parsed.get("question", ""), parsed.get("options", {})):
            q["images"] = []
            changed = True
            time.sleep(random.uniform(1.0, 2.0))
            continue

        # Extract image URLs from the HTML
        img_urls = extract_question_images(html, url)
        if not img_urls:
            q["images"] = []
            changed = True
            tqdm.write(f"    [INFO] exhibit ref but no <img> found: {url}")
            time.sleep(random.uniform(*DEFAULT_DELAY_RANGE))
            continue

        tqdm.write(f"    [IMG] {exam} {parsed.get('question_no', url)}: {len(img_urls)} image(s)")
        patched += 1

        if not dry_run:
            q_dir = base_img_dir / _question_dir_name(url)
            saved = download_images(img_urls, q_dir)
            q["images"] = saved
            changed = True
        else:
            q["images"] = img_urls  # store remote URLs in dry-run for preview

        time.sleep(random.uniform(*DEFAULT_DELAY_RANGE))

    if changed and not dry_run:
        json_path.write_text(json.dumps(questions, indent=2, ensure_ascii=False))
        tqdm.write(f"  Updated {json_path}")

    return patched, changed


# ── Git helpers ───────────────────────────────────────────────────────────────

def git_commit(provider: str, exam: str) -> None:
    json_path = DATA_DIR / provider / f"{exam}.json"
    img_dir = IMAGES_DIR / provider / exam
    try:
        paths = [str(json_path)]
        if img_dir.exists():
            paths.append(str(img_dir))
        subprocess.run(["git", "add"] + paths, check=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", f"images: {provider}/{exam}"],
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push"], check=True)
            tqdm.write(f"  Committed and pushed images for {provider}/{exam}")
    except Exception as e:
        tqdm.write(f"  [WARN] git commit failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and store images for exam questions that reference exhibits/figures."
    )
    parser.add_argument("--provider", required=True, help="Provider slug (e.g. ibm)")
    parser.add_argument("--exam", default=None, help="Limit to one exam slug (e.g. c1000-012)")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without downloading or writing")
    parser.add_argument("--commit", action="store_true", help="Git commit and push after each exam")
    parser.add_argument("--workers", type=int, default=2, help="Parallel workers for discussion page scanning (default: 2)")
    args = parser.parse_args()

    provider = normalize_provider(args.provider)
    provider_dir = DATA_DIR / provider
    if not provider_dir.exists():
        print(f"No data directory for provider '{provider}'")
        sys.exit(2)

    # Early exit if every question already has an "images" field
    if not args.exam:
        pending = 0
        for jf in provider_dir.glob("*.json"):
            try:
                qs = json.loads(jf.read_text())
                pending += sum(1 for q in qs if "images" not in q)
            except Exception:
                pass
        if pending == 0:
            print(f"All questions for '{provider}' already have images. Nothing to do.")
            sys.exit(2)

    cache = HtmlCache(Path(DEFAULT_CACHE_DIR), DEFAULT_CACHE_TTL_SECONDS)
    fetcher = HttpFetcher(
        timeout=DEFAULT_TIMEOUT_MS // 1000,
        retries=DEFAULT_RETRIES,
        cache=cache,
        refresh_cache=False,
    )

    print(f"Scanning discussion pages for '{provider}'...")
    scanner = FastDiscussionScanner(provider, fetcher, delay_range=DEFAULT_DELAY_RANGE)
    try:
        total_pages = scanner.get_num_pages()
    except Exception as e:
        print(f"Could not get page count: {e}")
        return

    page_numbers = build_page_numbers(total_pages, 1, None, None)
    print(f"  {total_pages} discussion pages found, scanning...")
    exam_links: Dict[str, List[str]] = scanner.scan_all_exams(page_numbers, workers=args.workers)

    if not exam_links:
        print("No exam links found.")
        return

    if args.exam:
        exam_links = {k: v for k, v in exam_links.items() if k == args.exam}
        if not exam_links:
            print(f"Exam '{args.exam}' not found in scanned discussions.")
            return

    # Only process exams that have a JSON file
    exam_links = {k: v for k, v in exam_links.items() if (provider_dir / f"{k}.json").exists()}
    print(f"\nProcessing {len(exam_links)} exam(s)...")

    total_patched = 0
    for exam, urls in sorted(exam_links.items()):
        print(f"\n[{provider}/{exam}] {len(urls)} questions")
        count, changed = process_exam(provider, exam, urls, fetcher, dry_run=args.dry_run)
        total_patched += count
        if changed and args.commit and not args.dry_run:
            git_commit(provider, exam)

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\nDone{suffix}. {total_patched} question(s) with images found across {len(exam_links)} exam(s).")


if __name__ == "__main__":
    main()
