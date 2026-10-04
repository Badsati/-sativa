import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from .settings import BASE_URL


def normalize_provider(provider: str) -> str:
    provider = (provider or "").strip()
    if not provider:
        return ""

    parsed = urlparse(provider)
    path = parsed.path if parsed.scheme else provider

    match = re.search(r"(?:^|/)discussions/([^/]+)", path.strip("/"), re.I)
    if match:
        return match.group(1).lower()

    match = re.search(r"(?:^|/)exams/([^/]+)", path.strip("/"), re.I)
    if match:
        return match.group(1).lower()

    return provider.strip("/").lower()


def normalize_search(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().upper()


def normalize_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def display_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def provider_discussion_url(provider: str, page_number: int = 1) -> str:
    base_url = f"{BASE_URL}/discussions/{normalize_provider(provider)}/"
    return f"{base_url}{page_number}/" if page_number > 1 else base_url


def matches_discussion_entry(text: str, href: str, search_string: str) -> bool:
    normalized_needle = normalize_search(search_string)
    slug_needle = normalize_slug(search_string)
    normalized_haystack = normalize_search(f"{text} {href}")
    slug_haystack = normalize_slug(f"{text} {href}")

    return normalized_needle in normalized_haystack or slug_needle in slug_haystack


def discussion_entry_url(text: str, href: str) -> str:
    absolute_url = urljoin(BASE_URL, href)
    parsed = urlparse(absolute_url)
    path_match = re.search(r"^(/discussions/[^/]+/view/)(\d+)-", parsed.path)

    if path_match and re.search(r"\btopic\s+\d+\s+question\s+\d+\b", text, re.I):
        title_slug = display_slug(text)
        return urljoin(BASE_URL, f"{path_match.group(1)}{path_match.group(2)}-{title_slug}/")

    return absolute_url


def build_page_numbers(
    total_pages: int,
    start_page: int,
    pages: Optional[int],
    end_page: Optional[int],
) -> List[int]:
    start = max(1, start_page)
    if end_page is not None:
        end = min(total_pages, max(start, end_page))
    elif pages is not None:
        end = min(total_pages, start + max(1, pages) - 1)
    else:
        end = total_pages
    return list(range(start, end + 1))


def extract_topic_question(link: str) -> Tuple[int, int]:
    match = re.search(r"topic-(\d+)-question-(\d+)", link, re.I)
    if not match:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2)))


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "-")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", path).strip("-")
    return slug or "examtopics-page"
