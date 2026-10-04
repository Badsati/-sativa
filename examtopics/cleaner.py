from bs4 import BeautifulSoup


def clean_examtopics_html(html: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for element in soup.select("#notRemoverPopup, .popup-overlay"):
        element.decompose()

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()
        if "notRemoverPopup" in script_text or "createPopup" in script_text:
            script.decompose()

    for style in soup.find_all("style"):
        style_text = style.string or style.get_text()
        if ".popup-overlay" in style_text or "#notRemoverPopup" in style_text:
            style.decompose()

    if soup.head:
        if not soup.head.find("base"):
            base_tag = soup.new_tag("base", href=source_url)
            soup.head.insert(0, base_tag)

        cleaner_style = soup.new_tag("style")
        cleaner_style.string = (
            "#notRemoverPopup,.popup-overlay{display:none!important;"
            "visibility:hidden!important;pointer-events:none!important}"
        )
        soup.head.append(cleaner_style)

    return str(soup)

