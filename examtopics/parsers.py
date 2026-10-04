import re
from html import unescape
from typing import List, Tuple

from bs4 import BeautifulSoup


def strip_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def is_blocked_html(html: str) -> bool:
    text = (html or "").lower()
    blocked_markers = (
        "checking your browser",
        "verify you are human",
        "please complete the security check",
        "g-recaptcha-response",
        "hcaptcha",
        "cf-chl",
        "access denied",
    )
    return any(marker in text for marker in blocked_markers)


def raise_if_blocked(html: str):
    if is_blocked_html(html):
        raise RuntimeError("Blocked by anti-bot/captcha page")


def extract_discussion_entries(html: str) -> List[Tuple[str, str]]:
    entries = []
    pattern = re.compile(
        r"<a\b(?=[^>]*\bdiscussion-link\b)(?=[^>]*\bhref=(?P<q>['\"])(?P<href>.*?)(?P=q))[^>]*>(?P<text>.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        entries.append((strip_html_text(match.group("text")), unescape(match.group("href"))))

    if entries:
        return entries

    soup = BeautifulSoup(html, "html.parser")
    return [
        (anchor.get_text(" ", strip=True), anchor.get("href") or "")
        for anchor in soup.select("a.discussion-link")
        if anchor.get("href")
    ]


def parse_discussion_page_count(html: str) -> int:
    text = strip_html_text(html)
    match = re.search(r"Discussions\s+Page\s+\d+\s+of\s+(\d+)", text, re.I)
    if match:
        return int(match.group(1))

    match = re.search(r"Page\s+\d+\s+of\s+(\d+)", text, re.I)
    if match:
        return int(match.group(1))

    soup = BeautifulSoup(html, "html.parser")
    indicator = soup.select_one(".discussion-list-page-indicator")
    if indicator:
        strong_values = [
            item.get_text(strip=True)
            for item in indicator.find_all("strong")
            if item.get_text(strip=True).isdigit()
        ]
        if len(strong_values) >= 2:
            return int(strong_values[1])

        match = re.search(r"Page\s+\d+\s+of\s+(\d+)", indicator.get_text(" ", strip=True), re.I)
        if match:
            return int(match.group(1))

    raise RuntimeError("Could not find discussion page count")

