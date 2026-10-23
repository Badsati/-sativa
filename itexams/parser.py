import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    # <div class="examsPage__nav-r">
    #   <div><span>Page:</span> <span>1 </span> / <span>95</span></div>
    nav = soup.find(class_="examsPage__nav-r")
    if nav:
        spans = nav.find_all("span")
        # spans[0]="Page:", spans[1]="1", spans[2]="95"
        if len(spans) >= 3:
            try:
                return int(spans[2].get_text().strip())
            except ValueError:
                pass
    return 1


def parse_total_questions(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    nav = soup.find(class_="examsPage__nav-r")
    if nav:
        m = re.search(r"Total\s+(\d+)\s+question", nav.get_text(), re.I)
        if m:
            return int(m.group(1))
    return None


def _parse_option(span) -> Tuple[str, str]:
    text = _clean(span.get_text())
    m = re.match(r"^([A-Z])\.\s*(.+)$", text)
    if m:
        return m.group(1), m.group(2)
    return "", text


def parse_exam_page(html: str, page_url: str = "") -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    questions = []

    for card in soup.select("div.examsPage__question"):
        q: Dict = {"url": page_url}

        # "Question #1 (Topic: Single Topic)" → "Question #1"
        title_el = card.select_one("span.title")
        if title_el:
            title = _clean(title_el.get_text())
            m = re.match(r"(Question\s*#\d+)", title)
            q["question_no"] = m.group(1) if m else title
        else:
            q["question_no"] = ""

        # Question text — inline-styled span in body
        body = card.select_one(".examsPage__question-body")
        q_text = ""
        if body:
            text_span = body.find("span", style=True)
            if text_span:
                for br in text_span.find_all("br"):
                    br.replace_with(" ")
                q_text = _clean(text_span.get_text(" "))
        q["question"] = q_text

        # Options — "A. Some text" spans
        options: Dict[str, str] = {}
        for span in card.select("span.examsPage__question-answer"):
            letter, text = _parse_option(span)
            if letter:
                options[letter] = text
        q["options"] = options

        # Answer — <strong>B</strong> or <strong><img></strong>
        answer_div = card.select_one(".examsPage__question-correct-answer")
        answer = ""
        if answer_div:
            strong = answer_div.find("strong")
            if strong:
                if strong.find("img"):
                    answer = "[image]"
                else:
                    answer = _clean(strong.get_text())

        q["most_voted"] = answer
        q["vote_counts"] = {}
        q["discussion"] = []

        if answer and answer != "[image]" and answer in options:
            q["community_answer"] = f"{answer}. {options[answer]}"
        elif answer:
            q["community_answer"] = answer
        else:
            q["community_answer"] = "Not available"

        questions.append(q)

    return questions
