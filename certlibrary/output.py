import json
from typing import Dict, List

_DIVIDER = "═" * 60


def format_question(q: Dict, index: int) -> str:
    lines = []
    lines.append(_DIVIDER)
    lines.append(f"QUESTION #{index}    {q.get('question_no', '')}")
    lines.append(_DIVIDER)
    lines.append("")
    lines.append(f"Question:\n{q.get('question', '(not found)')}")
    lines.append("")

    options = q.get("options", {})
    if options:
        lines.append("Options:")
        for letter, text in options.items():
            lines.append(f"  {letter}. {text}")
        lines.append("")

    answer = q.get("community_answer", "")
    if answer:
        lines.append(f"Correct Answer:\n  {answer}")
        lines.append("")

    lines.append(f"URL: {q.get('url', '')}")
    lines.append("")

    return "\n".join(lines)


def write_questions_to_txt(filename: str, questions: List[Dict]):
    with open(filename, "w", encoding="utf-8") as f:
        for i, q in enumerate(questions, 1):
            f.write(format_question(q, i))
            f.write("\n")
    print(f"✓ Saved {len(questions)} questions → {filename}")


def write_questions_to_json(filename: str, questions: List[Dict]):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    print(f"✓ Saved {len(questions)} questions → {filename}")
