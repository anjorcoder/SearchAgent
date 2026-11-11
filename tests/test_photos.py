import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from photos import candidate_pages, find_photo, guessed_pages, pick_image, pick_og_image

THEORG_LIKE = (
    '<meta property="og:image" content="https://cdn.theorg.com/og/position?name=Martijn+Wagner&amp;'
    'image=https%3A%2F%2Fcdn.theorg.com%2Fabc_thumb.jpg&amp;company=Valcon"/>'
    '<img alt="Valcon logo" src="https://cdn.theorg.com/logo_thumb.jpg">'
)


def test_pick_og_image_extracts_portrait_from_social_card():
    assert pick_og_image(THEORG_LIKE, "https://theorg.com/x", "Martijn Wagner") == "https://cdn.theorg.com/abc_thumb.jpg"


def test_pick_og_image_ignores_og_without_name():
    html = '<meta property="og:image" content="https://site.nl/scope-small.png">'
    assert pick_og_image(html, "https://site.nl/manager/ruud-joosten", "Ruud Joosten") is None


def test_pick_og_image_plain_url_with_name():
    html = '<meta property="og:image" content="/img/ruud-joosten.jpg">'
    assert pick_og_image(html, "https://site.nl/a", "Ruud Joosten") == "https://site.nl/img/ruud-joosten.jpg"


def test_guessed_pages():
    assert guessed_pages("Martijn Wagner", "Valcon") == ["https://theorg.com/org/valcon/org-chart/martijn-wagner"]
    assert guessed_pages("Martijn Wagner", "") == [] and guessed_pages("Jordan", "BAM") == []


def test_find_photo_extra_page_must_mention_organisation():
    page = THEORG_LIKE + "<p>Associate Partner at Valcon</p>"
    p = {"profielen": [], "loopbaan": []}
    kw = dict(fetch_html=lambda u: page, is_image=lambda u: True)
    assert find_photo("Martijn Wagner", p, extra_pages=["https://theorg.com/p"], organisatie="BAM", **kw) is None
    assert find_photo("Martijn Wagner", p, extra_pages=["https://theorg.com/p"], organisatie="valcon", **kw)


def test_find_photo_uses_og_after_img_search_and_extra_pages():
    pages = {"https://bam.com/eb": "<img src='/x.jpg' alt='iemand'>", "https://theorg.com/p": THEORG_LIKE}
    p = {"huidige_functie": {"bron_url": "https://bam.com/eb"}, "profielen": [], "loopbaan": []}
    assert find_photo("Martijn Wagner", p, fetch_html=pages.get, is_image=lambda u: True) is None
    extra = ["https://www.linkedin.com/in/x", "https://theorg.com/p"]
    got = find_photo("Martijn Wagner", p, extra_pages=extra, fetch_html=pages.get, is_image=lambda u: True)
    assert got == "https://cdn.theorg.com/abc_medium.jpg"
    # bestaat de grotere variant niet, dan de thumbnail
    got = find_photo("Martijn Wagner", p, extra_pages=extra, fetch_html=pages.get, is_image=lambda u: "_medium" not in u)
    assert got == "https://cdn.theorg.com/abc_thumb.jpg"

BAM_LIKE = """
<img src="/logo.svg" alt="BAM">
<img srcset="data:image/gif;base64,xx 1w, /f/tiny/ruud-joosten-2026.jpg?h=1&amp;i=2 6w,
 /f/small/ruud-joosten-2026.jpg 400w, /f/huge/ruud-joosten-2026.jpg 2000w" alt="">
<img src="/f/henri.jpg" alt="Henri de Pater">
"""


def test_pick_image_by_filename_prefers_srcset_up_to_800():
    assert pick_image(BAM_LIKE, "https://www.bam.com/a/b", "Ruud Joosten") == "https://www.bam.com/f/small/ruud-joosten-2026.jpg"


def test_pick_image_by_alt():
    html = '<img src="https://cdn.x/1372.jpg" alt="Ruud Joosten"><img src="/joosten-groep.png">'
    assert pick_image(html, "https://x.nl", "Ruud Joosten") == "https://cdn.x/1372.jpg"


def test_pick_image_none_and_single_name_needs_full_match():
    assert pick_image('<img src="/a.jpg" alt="Iemand">', "https://x.nl", "Ruud Joosten") is None
    assert pick_image('<img src="/jordan-river.jpg">', "https://x.nl", "Jordan") is None
    assert pick_image('<img src="/a.jpg" alt="Michael Jordan">', "https://x.nl", "Jordan") == "https://x.nl/a.jpg"


def test_candidate_pages_skips_linkedin_and_dedupes():
    p = {
        "huidige_functie": {"bron_url": "https://bam.com/eb"},
        "profielen": [{"url": "https://www.linkedin.com/in/x"}, {"url": "https://bam.com/eb"}],
        "loopbaan": [{"bron_url": "https://scope.nl/m"}],
    }
    assert candidate_pages(p) == ["https://bam.com/eb", "https://scope.nl/m"]


def test_find_photo_uses_agent_url_when_it_is_an_image():
    p = {"foto_url": "https://x.nl/f.jpg", "profielen": []}
    assert find_photo("A B", p, fetch_html=lambda u: "", is_image=lambda u: True) == "https://x.nl/f.jpg"


def test_find_photo_falls_back_to_pages_and_survives_errors():
    pages = {"https://a.nl": None, "https://b.nl": '<img src="/a-b.jpg">'}

    def fetch(u):
        if pages[u] is None:
            raise OSError("down")
        return pages[u]

    p = {"foto_url": "https://x.nl/page.html", "huidige_functie": {"bron_url": "https://a.nl"},
         "profielen": [{"url": "https://b.nl"}], "loopbaan": []}
    assert find_photo("A B", p, fetch_html=fetch, is_image=lambda u: u.endswith(".jpg")) == "https://b.nl/a-b.jpg"
