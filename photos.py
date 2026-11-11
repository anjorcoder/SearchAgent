"""Zoekt een officiële portretfoto op de profielpagina's die de agent vond.

Web search levert zelden directe afbeeldingslinks op, maar bedrijfspagina's
hebben meestal een <img> met de naam van de persoon in alt-tekst of bestandsnaam.
"""
import html
import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

TIMEOUT = 8
MAX_PAGES = 6
MAX_BYTES = 3_000_000
SKIP_HOSTS = ("linkedin.com", "bloomberg.com", "twitter.com", "x.com", "facebook.com")
UA = {"User-Agent": "Mozilla/5.0 (StakeholderMonitor)"}


class _ImgParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.imgs = []
        self.og_image = ""

    def handle_starttag(self, tag, attrs):
        a = {k: html.unescape(v or "") for k, v in attrs}
        if tag == "img":
            self.imgs.append(a)
        elif tag == "meta" and a.get("property") == "og:image" and not self.og_image:
            self.og_image = a.get("content", "")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _best_src(img):
    """Kiest uit srcset de grootste variant tot ~800px, anders src/data-src."""
    options = []
    for part in (img.get("srcset") or img.get("data-srcset") or "").split(","):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].endswith("w") and not bits[0].startswith("data:"):
            try:
                options.append((int(bits[1][:-1]), bits[0]))
            except ValueError:
                pass
    if options:
        fitting = [o for o in options if o[0] <= 800] or options
        return max(fitting)[1]
    for key in ("src", "data-src", "data-original"):
        src = img.get(key, "")
        if src and not src.startswith("data:"):
            return src
    return ""


def pick_image(page_html, base_url, naam):
    """Geeft de absolute URL van de afbeelding die het best bij `naam` past, of None."""
    tokens = naam.lower().split()
    if not tokens:
        return None
    full, slug, surname = naam.lower(), _slug(naam), tokens[-1]
    parser = _ImgParser()
    parser.feed(page_html)
    best, best_score = None, 0
    for img in parser.imgs:
        src = _best_src(img)
        if not src:
            continue
        label = f"{img.get('alt', '')} {img.get('title', '')}".lower()
        filename = _slug(urlparse(src).path.rsplit("/", 1)[-1])
        score = 0
        # Bij één naamdeel ("Jordan") is een bestandsnaam te dubbelzinnig; dan alleen de alt-tekst.
        if full in label or (len(tokens) > 1 and slug in filename):
            score = 3
        elif len(tokens) > 1 and (surname in label or surname in filename):
            score = 1
        if score > best_score:
            best, best_score = urljoin(base_url, src), score
    return best


def pick_og_image(page_html, base_url, naam):
    """Deelafbeelding (og:image) van de pagina, alleen als de naam in die link staat.

    Sites als theorg.com laden het portret pas in de browser, maar zetten het in een
    gegenereerde deelkaart: .../og/position?name=Martijn+Wagner&image=<portret>. Zonder
    naam in de link is het meestal een logo of artikelfoto, dus dan niets.
    """
    tokens = naam.lower().split()
    if len(tokens) < 2:
        return None
    parser = _ImgParser()
    parser.feed(page_html)
    og = parser.og_image
    if not og or tokens[-1] not in unquote(og).lower().replace("+", " "):
        return None
    inner = parse_qs(urlparse(og).query).get("image", [""])[0]
    return inner if inner.startswith(("http://", "https://")) else urljoin(base_url, og)


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def _is_image(url):
    try:
        with _get(url) as r:
            return r.headers.get_content_type().startswith("image/")
    except Exception:
        return False


def _fetch_html(url):
    with _get(url) as r:
        if r.headers.get_content_type() != "text/html":
            return ""
        return r.read(MAX_BYTES).decode(r.headers.get_content_charset() or "utf-8", "replace")


def _usable(urls):
    seen, result = set(), []
    for u in urls:
        host = urlparse(u).hostname or ""
        if not u or u in seen or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        seen.add(u)
        result.append(u)
    return result


def candidate_pages(profiel, extra_pages=()):
    """Bronpagina's uit het profiel (max. MAX_PAGES), daarna extra gezochte persoonspagina's."""
    urls = []
    if profiel.get("huidige_functie"):
        urls.append(profiel["huidige_functie"]["bron_url"])
    urls += [p["url"] for p in profiel.get("profielen", [])]
    urls += [i["bron_url"] for i in profiel.get("loopbaan", [])]
    own = _usable(urls)[:MAX_PAGES]
    return own + [u for u in _usable(extra_pages) if u not in own][:MAX_PAGES]


def guessed_pages(naam, organisatie):
    """Persoonspagina's met een vast URL-patroon, die web search vaak niet vindt."""
    if not organisatie or len(naam.split()) < 2:
        return []
    return [f"https://theorg.com/org/{_slug(organisatie)}/org-chart/{_slug(naam)}"]


def _mentions(page_html, organisatie):
    return bool(re.search(rf"\b{re.escape(organisatie)}\b", page_html, re.I))


def find_photo(naam, profiel, extra_pages=(), organisatie="", fetch_html=_fetch_html, is_image=_is_image):
    """Eerst de foto_url van de agent, dan een <img> met de naam, dan een deelafbeelding met de naam.

    Extra pagina's komen niet uit het (gecontroleerde) profiel; daarom moeten die de
    organisatie noemen, anders kan het een naamgenoot zijn.
    """
    if profiel.get("foto_url") and is_image(profiel["foto_url"]):
        return profiel["foto_url"]
    extra = set(_usable(extra_pages))
    og_candidates = []
    for page in candidate_pages(profiel, extra_pages):
        try:
            page_html = fetch_html(page) or ""
        except Exception:
            continue
        if page in extra and organisatie and not _mentions(page_html, organisatie):
            continue
        found = pick_image(page_html, page, naam)
        if found and is_image(found):
            return found
        og = pick_og_image(page_html, page, naam)
        if og:
            # theorg-thumbnails zijn 200px; de _medium-variant is groter als die bestaat
            og_candidates += [og.replace("_thumb.", "_medium."), og] if "_thumb." in og else [og]
    return next((og for og in og_candidates if is_image(og)), None)
