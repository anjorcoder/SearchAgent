# Stakeholder Monitor – ontwerp

Datum: 2026-10-22

## Doel
Een lokale website (localhost) waarmee je zakelijke stakeholders beheert. Op knopdruk zoekt een agent op het internet naar recente updates over die persoon, uitsluitend uit openbare, zakelijke bronnen. Resultaten worden bewaard en getoond met bronlink.

## Keuzes
- **Zoekmethode:** Claude API met de web search tool (samenvatting + bronnen).
- **Stack:** Python (Flask) backend, gewone HTML/JS frontend. Node.js is niet geïnstalleerd, Python wel.
- **Opslag:** één JSON-bestand (`data/stakeholders.json`). Geen database, geen native dependencies.
- **Scope stakeholders:** zakelijke contacten (naam, functie, organisatie, zoekfocus).
- **Gebruik:** één gebruiker, alleen lokaal (bind op 127.0.0.1).

## Architectuur
```
Browser (127.0.0.1:5000)  ->  Flask-server  ->  Claude API (web_search)
   static/index.html            storage.py (JSON)
```

### Componenten
| Bestand | Verantwoordelijkheid |
|---|---|
| `app.py` | Flask-routes, serveert de frontend |
| `storage.py` | Lezen/schrijven van het JSON-bestand (atomisch schrijven, opschonen van oude updates) |
| `agent.py` | Roept Claude aan met web search, geeft gestructureerde updates terug |
| `static/index.html` (+ js/css) | UI: lijst, formulier, zoekknop, resultaten |
| `.env` | `ANTHROPIC_API_KEY` (nooit in de browser, niet in versiebeheer) |

### API
- `GET /api/stakeholders` – lijst incl. updates
- `POST /api/stakeholders` – toevoegen (naam, functie, organisatie, zoekfocus)
- `DELETE /api/stakeholders/<id>` – persoon en alle bijbehorende updates verwijderen
- `POST /api/stakeholders/<id>/search` – start de agent, slaat nieuwe updates op, geeft ze terug

### Datamodel (JSON)
```json
{
  "stakeholders": [
    {
      "id": "uuid",
      "naam": "...", "functie": "...", "organisatie": "...", "zoekfocus": "...",
      "laatst_gezocht": "ISO-datum",
      "updates": [
        { "titel": "...", "samenvatting": "...", "bron_url": "...",
          "gepubliceerd": "ISO-datum of null", "opgehaald": "ISO-datum" }
      ]
    }
  ]
}
```
Updates met dezelfde `bron_url` worden niet dubbel opgeslagen.

## Agent
- Input: naam, functie, organisatie, zoekfocus.
- Prompt beperkt de agent tot professionele informatie (functiewijziging, projecten, persberichten, interviews, publicaties, aanbestedingen) en sluit privé-informatie expliciet uit (gezondheid, familie, politieke/religieuze voorkeur, woonadres, financiële privésituatie, enz.).
- Bij twijfel over of een resultaat dezelfde persoon betreft (naamgenoten) wordt het weggelaten of gemarkeerd als onzeker.
- Output: JSON-lijst van updates met bron-URL; zonder bron wordt niets opgeslagen.
- Alleen vrij toegankelijke webpagina's; geen logins, geen scraping achter authenticatie.

## Wet- en regelgeving (AVG)
Openbaar is niet gelijk aan vrij te verwerken; de AVG blijft gelden.
- Alleen zoeken op knopdruk, geen achtergrondmonitoring.
- Dataminimalisatie: updates ouder dan 12 maanden worden automatisch verwijderd (instelbaar).
- Bronvermelding bij elke update; verwijderen van een persoon wist alles.
- Interface toont een korte noot over alleen openbare, zakelijke bronnen.
- Buiten scope van de software: grondslag (bijv. gerechtvaardigd belang), verwerkingsregister en eventuele DPIA. Afstemmen met privacy officer / FG.

## Foutafhandeling
- Ontbrekende API-key: duidelijke melding bij opstarten en in de UI.
- API-fout of timeout: foutmelding bij de betreffende kaart, bestaande data blijft intact.
- Corrupt of ontbrekend JSON-bestand: ontbrekend wordt aangemaakt; corrupt wordt hernoemd tot back-up en er wordt met een leeg bestand gestart.

## Testen
- `storage.py`: unit tests (toevoegen, dedupliceren, verwijderen, opschonen oude updates, corrupt bestand).
- `agent.py`: test met gemockte API-respons (parsen, ontbrekende bron weglaten).
- Handmatige test in de browser met één echte stakeholder.

## Buiten scope (YAGNI)
Inloggen/multi-user, geplande automatische runs, e-mailmeldingen, export, social-media-integraties.
