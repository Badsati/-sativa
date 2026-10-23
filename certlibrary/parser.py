import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    area = soup.select_one(".pagination_area")
    if area:
        m = re.search(r"/\s*(\d+)", area.get_text())
        if m:
            return int(m.group(1))
    return 1


def parse_total_questions(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    right = soup.select_one(".right_area_text")
    if right:
        m = re.search(r"(\d+)\s+question", right.get_text(), re.I)
        if m:
            return int(m.group(1))
    return None


def _first_question_no(html: str) -> Optional[str]:
    """Return the question_no of the first question on the page, used to detect redirect loops."""
    soup = BeautifulSoup(html, "html.parser")
    card = soup.select_one("div.card.question_card")
    if card:
        h5 = card.select_one("div.card-header h5")
        if h5:
            return _clean(h5.get_text())
    return None


def parse_exam_page(html: str, page_url: str = "") -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    questions = []

    for card in soup.select("div.card.question_card"):
        q: Dict = {"url": page_url}

        # Question number — "Question 1"
        h5 = card.select_one("div.card-header h5")
        q["question_no"] = _clean(h5.get_text()) if h5 else ""

        # Question text
        q_text_el = card.select_one(".question_text p")
        if q_text_el:
            for br in q_text_el.find_all("br"):
                br.replace_with(" ")
            q["question"] = _clean(q_text_el.get_text(" "))
        else:
            q["question"] = ""

        # Options — li[data-choice="A"] with letter from attr, text after stripping <strong>
        options: Dict[str, str] = {}
        for li in card.select("ul.choices-list li"):
            letter = (li.get("data-choice") or "").strip()
            if not letter:
                continue
            strong = li.select_one("strong")
            if strong:
                strong.decompose()
            options[letter] = _clean(li.get_text(" "))
        q["options"] = options

        # Answer — data-answer attr on .answer_block; value is a letter or an <img> tag
        answer_el = card.select_one(".answer_block")
        answer = ""
        if answer_el:
            raw = (answer_el.get("data-answer") or "").strip()
            if raw and not raw.startswith("<"):
                answer = raw
            elif raw.startswith("<"):
                answer = "[image]"

        q["most_voted"] = answer
        q["vote_counts"] = {}
        q["discussion"] = []

        if answer and answer in options:
            q["community_answer"] = f"{answer}. {options[answer]}"
        elif answer:
            q["community_answer"] = answer
        else:
            q["community_answer"] = "Not available"

        questions.append(q)

    return questions
