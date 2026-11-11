"""Zoekagent: gebruikt Claude met web search om een zakelijk profiel en openbare updates te vinden."""
import json
import os
import re

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 6
# Web search via code execution telt elke parallelle zoekopdracht mee; 5 was in één batch op.
PROFILE_MAX_SEARCHES = 20
UPDATES_MAX_SEARCHES = 15

UPDATE_TYPES = [
    "benoeming", "project", "interview", "persbericht",
    "publicatie", "toespraak", "bedrijfsnieuws", "overig",
]

BASE_RULES = """Je bent een onderzoeksassistent voor een commerciële afdeling.

Regels (verplicht):
- Gebruik uitsluitend openbaar toegankelijke bronnen (nieuws, bedrijfswebsites, persberichten, jaarverslagen, publieke LinkedIn-profielen en -posts, KvK/handelsregister, aanbestedingen, vakmedia).
- Rapporteer ALLEEN professionele informatie.
- Rapporteer NOOIT privé-informatie: gezondheid, familie en relaties, woonadres, privé-financiën, politieke of religieuze voorkeur, seksuele geaardheid, strafrechtelijk verleden, of andere bijzondere persoonsgegevens.
- Controleer dat informatie echt over deze persoon gaat (naam + organisatie/functie). Laat twijfelgevallen over naamgenoten weg.
- Klopt de opgegeven functie of organisatie niet met wat je vindt (bijv. iemand is CEO van het moederbedrijf in plaats van de genoemde dochter), meld dat dan in de toelichting.
- Verzin niets. Elk item moet een bron_url hebben die je daadwerkelijk hebt gevonden.
- Een zoekfocus is een voorkeur voor waar je extra op let, geen filter.
- Schrijf in het Nederlands.
- Lever je eindresultaat uitsluitend aan via de tool `rapporteer`, precies één keer, als laatste stap.
- Gebruik `toelichting` om kort uit te leggen wat opvalt: waarom je weinig vond, twijfel over identiteit, of afwijkende functie/organisatie. Laat leeg als er niets bijzonders is."""

PROFILE_SYSTEM = BASE_RULES + """

Taak: stel een zakelijk profiel op van de persoon: huidige functie, loopbaan, opleiding, nevenfuncties en bestuursrollen, thema's waarover de persoon publiekelijk spreekt, en officiële online profielen. Er is geen tijdsgrens: ook oudere loopbaaninformatie is relevant.

- `samenvatting`: één korte zin die de persoon zakelijk typeert.
- `kernpunten`: 3-6 korte bulletpoints (elk max. ~20 woorden) met het belangrijkste om te weten vóór een gesprek.
- `profielen`: officiële profielen, en vooral pagina's die specifiek over deze persoon gaan (teamprofiel op de bedrijfswebsite, theorg.com, sprekersbio), niet alleen LinkedIn.
- `foto_url`: een directe URL naar een afbeeldingsbestand (jpg/png/webp) van een officiële zakelijke portretfoto, bij voorkeur van de website of persberichten van de werkgever. Geen foto's uit privécontext. null als je die niet vindt."""

UPDATES_SYSTEM = BASE_RULES + """

Taak: zoek recente, openbare, zakelijke updates over de persoon: functiewijzigingen, projecten, opdrachten, persberichten, interviews, publicaties, toespraken, benoemingen, bedrijfsnieuws waarin de persoon voorkomt. Alleen updates van de afgelopen 12 maanden. Neem zoveel relevante updates op als je kunt onderbouwen, nieuwste eerst."""

PHOTO_SYSTEM = BASE_RULES + """

Taak: vind maximaal 5 openbare webpagina's die specifiek over deze persoon gaan en waarschijnlijk een zakelijke portretfoto tonen: een medewerkers- of teamprofiel op de website van de werkgever, een persoonspagina op theorg.com, een sprekersbio bij een evenement, of een interview met portret. Geef de pagina's zelf, geen afbeeldingslinks. Geen LinkedIn, Facebook, Instagram of X: die zijn niet automatisch te lezen."""

PHOTO_MAX_SEARCHES = 12  # parallelle zoekopdrachten tellen los mee; 5 was in één batch op

NUDGE = "Lever je resultaat nu aan via de tool rapporteer."


def _str(nullable=False):
    return {"type": ["string", "null"]} if nullable else {"type": "string"}


def _obj(props):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "properties": props,
    }


def _report_tool(schema):
    return {
        "name": "rapporteer",
        "description": "Lever je eindresultaat aan. Roep dit precies één keer aan, als laatste stap.",
        "strict": True,
        "input_schema": schema,
    }


PROFILE_TOOL = _report_tool(
    _obj(
        {
            "profiel": _obj(
                {
                    "samenvatting": _str(),
                    "kernpunten": {"type": "array", "items": _str()},
                    "foto_url": _str(True),
                    "huidige_functie": {
                        "anyOf": [
                            _obj({"functie": _str(), "organisatie": _str(), "sinds": _str(True), "bron_url": _str()}),
                            {"type": "null"},
                        ]
                    },
                    "loopbaan": {"type": "array", "items": _obj(
                        {"functie": _str(), "organisatie": _str(), "periode": _str(True), "bron_url": _str()})},
                    "opleiding": {"type": "array", "items": _obj(
                        {"opleiding": _str(), "instelling": _str(), "periode": _str(True), "bron_url": _str()})},
                    "nevenfuncties": {"type": "array", "items": _obj(
                        {"rol": _str(), "organisatie": _str(), "periode": _str(True), "bron_url": _str()})},
                    "thema_s": {"type": "array", "items": _obj(
                        {"thema": _str(), "toelichting": _str(), "bron_url": _str()})},
                    "profielen": {"type": "array", "items": _obj({"label": _str(), "url": _str()})},
                }
            ),
            "toelichting": _str(),
        }
    )
)

UPDATES_TOOL = _report_tool(
    _obj(
        {
            "updates": {
                "type": "array",
                "items": _obj(
                    {
                        "titel": _str(),
                        "type": {"type": "string", "enum": UPDATE_TYPES},
                        "samenvatting": _str(),
                        "bron_url": _str(),
                        "gepubliceerd": _str(True),
                    }
                ),
            },
            "toelichting": _str(),
        }
    )
)


PHOTO_TOOL = _report_tool(
    _obj({"paginas": {"type": "array", "items": _str()}, "toelichting": _str()})
)


def build_prompt(stakeholder, taak):
    regels = [f"Naam: {stakeholder['naam']}"]
    if stakeholder.get("functie"):
        regels.append(f"Functie: {stakeholder['functie']}")
    if stakeholder.get("organisatie"):
        regels.append(f"Organisatie: {stakeholder['organisatie']}")
    if stakeholder.get("zoekfocus"):
        regels.append(f"Zoekfocus: {stakeholder['zoekfocus']}")
    return f"{taak}\n" + "\n".join(regels)


# -- opschonen ---------------------------------------------------------------
def _url(value):
    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _s(value):
    return str(value or "").strip()


def _clean_items(items, fields, url_key="bron_url"):
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = _url(item.get(url_key))
        if not url:
            continue
        rec = {f: (_s(item.get(f)) or None) for f in fields}
        rec[url_key] = url
        result.append(rec)
    return result


def clean_updates(items):
    """Houdt alleen updates met een geldige bron_url over; onbekend type wordt 'overig'."""
    result = []
    for u in _clean_items(items, ["titel", "type", "samenvatting", "gepubliceerd"]):
        u["titel"] = u["titel"] or ""
        u["samenvatting"] = u["samenvatting"] or ""
        if u["type"] not in UPDATE_TYPES:
            u["type"] = "overig"
        result.append(u)
    return result


def clean_profile(p):
    """Normaliseert een profiel: items zonder geldige bron vervallen, lijsten zijn altijd aanwezig."""
    p = p if isinstance(p, dict) else {}
    huidig = _clean_items([p.get("huidige_functie")], ["functie", "organisatie", "sinds"])
    return {
        "samenvatting": _s(p.get("samenvatting")),
        "kernpunten": [_s(k) for k in (p.get("kernpunten") or []) if isinstance(k, str) and _s(k)],
        "foto_url": _url(p.get("foto_url")) or None,
        "huidige_functie": huidig[0] if huidig else None,
        "loopbaan": _clean_items(p.get("loopbaan"), ["functie", "organisatie", "periode"]),
        "opleiding": _clean_items(p.get("opleiding"), ["opleiding", "instelling", "periode"]),
        "nevenfuncties": _clean_items(p.get("nevenfuncties"), ["rol", "organisatie", "periode"]),
        "thema_s": _clean_items(p.get("thema_s"), ["thema", "toelichting"]),
        "profielen": _clean_items(p.get("profielen"), ["label"], url_key="url"),
    }


def parse_json(text):
    """Terugvaloptie: haalt een JSON-object uit vrije tekst."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Geen JSON in antwoord van de agent")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"Ongeldige JSON van de agent: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Onverwachte JSON van de agent")
    return data


# -- zoeklus -----------------------------------------------------------------
def _final_text(message):
    return "".join(b.text for b in message.content if b.type == "text")


def _report(message):
    for b in message.content:
        if b.type == "tool_use" and b.name == "rapporteer":
            return b.input
    return None


def _run(system, prompt, report_tool, max_searches, client):
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}, report_tool]
    nudged = False

    for _ in range(MAX_CONTINUATIONS):
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise RuntimeError("De agent weigerde deze zoekopdracht.")
        report = _report(message)
        if report is not None:
            return report
        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        if not nudged:
            nudged = True
            messages.append({"role": "assistant", "content": message.content})
            messages.append({"role": "user", "content": NUDGE})
            continue
        try:
            return parse_json(_final_text(message))
        except ValueError:
            break

    raise RuntimeError("De agent gaf geen bruikbaar resultaat; probeer het opnieuw.")


def search_profile(stakeholder, client=None):
    client = client or anthropic.Anthropic()
    prompt = build_prompt(stakeholder, "Stel een zakelijk profiel op van deze persoon.")
    data = _run(PROFILE_SYSTEM, prompt, PROFILE_TOOL, PROFILE_MAX_SEARCHES, client)
    return {"profiel": clean_profile(data.get("profiel")), "toelichting": _s(data.get("toelichting"))}


def search_photo_pages(stakeholder, profiel=None, client=None):
    """Zoekt persoonspagina's waarop een portret staat; de fotozoeker haalt daar de afbeelding uit."""
    client = client or anthropic.Anthropic()
    s = dict(stakeholder)
    huidig = (profiel or {}).get("huidige_functie")
    if huidig:  # het profiel weet de functie vaak preciezer dan wat de gebruiker invulde
        s["functie"], s["organisatie"] = huidig.get("functie") or s.get("functie"), huidig.get("organisatie") or s.get("organisatie")
    prompt = build_prompt(s, "Zoek openbare persoonspagina's met een zakelijke portretfoto van deze persoon.")
    data = _run(PHOTO_SYSTEM, prompt, PHOTO_TOOL, PHOTO_MAX_SEARCHES, client)
    return [u for u in (_url(p) for p in data.get("paginas") or []) if u]


def search_updates(stakeholder, client=None):
    client = client or anthropic.Anthropic()
    prompt = build_prompt(stakeholder, "Zoek recente, openbare, zakelijke updates over deze persoon.")
    data = _run(UPDATES_SYSTEM, prompt, UPDATES_TOOL, UPDATES_MAX_SEARCHES, client)
    return {"updates": clean_updates(data.get("updates")), "toelichting": _s(data.get("toelichting"))}
