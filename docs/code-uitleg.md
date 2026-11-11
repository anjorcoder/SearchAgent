# Stakeholder Monitor – uitleg van de Python-bestanden

De Stakeholder Monitor is een lokale webapp. Je voegt een stakeholder toe, laat een zakelijk profiel opbouwen en zoekt recente openbare updates. Alles draait op je eigen computer (`127.0.0.1:5000`). Alleen de zoekopdrachten gaan via de Claude API naar buiten.

## Hoe de onderdelen samenwerken

```
 browser (static/index.html)
        │  fetch /api/...
        ▼
 app.py ──────────────► storage.py ──► data/stakeholders.json
   │  Flask-server        opslag
   │
   ├──► agent.py   Claude + web search: profiel, updates, persoonspagina's
   │
   └──► photos.py  haalt een portretfoto van de gevonden pagina's
```

| Bestand | Rol in één zin |
|---|---|
| [app.py](../app.py) | Webserver: serveert de pagina en verbindt de API-routes met opslag, agent en fotozoeker. |
| [agent.py](../agent.py) | Laat Claude met web search het profiel, de updates en persoonspagina's zoeken, en schoont het resultaat op. |
| [storage.py](../storage.py) | Leest en schrijft de stakeholders in één JSON-bestand en verwijdert oude updates. |
| [photos.py](../photos.py) | Zoekt een officiële portretfoto op webpagina's, zonder AI. |
| [tests/](../tests/) | Automatische tests voor alle bovenstaande bestanden, zonder echte API-aanroepen. |

---

## app.py – de webserver

**Wat het doet.** Start een Flask-server die alleen op je eigen computer bereikbaar is. De server levert de pagina (`static/index.html`) en de afbeeldingen, en biedt een kleine API aan die de pagina aanroept.

Bij het opstarten leest de server het `.env`-bestand (met `ANTHROPIC_API_KEY` en eventueel `CLAUDE_MODEL`). Dat gebeurt vóórdat `agent.py` wordt geladen, zodat de modelkeuze uit `.env` meetelt.

**API-routes**

| Route | Wat er gebeurt |
|---|---|
| `GET /` | Levert de webpagina. |
| `GET /api/stakeholders` | Lijst van alle stakeholders, met profiel en updates. |
| `POST /api/stakeholders` | Nieuwe stakeholder toevoegen. Alleen de naam is verplicht. |
| `DELETE /api/stakeholders/<id>` | Stakeholder met al zijn gegevens verwijderen. |
| `POST /api/stakeholders/<id>/profile` | Profiel opbouwen. De agent zoekt het profiel, de fotozoeker zoekt een portret, en het resultaat wordt opgeslagen. |
| `POST /api/stakeholders/<id>/search` | Updates zoeken. Nieuwe updates worden toegevoegd, en updates die je al had, worden overgeslagen. |
| `POST /api/stakeholders/<id>/photo` | Alleen opnieuw een foto zoeken. Werkt pas als er een profiel is. |

**Belangrijke hulpfuncties**

- `_precheck(id)`: controleert vóór een zoekactie of de stakeholder bestaat en of de API-sleutel aanwezig is.
- `_find_photo(s, profiel)`: de fotoroute in drie stappen, van goedkoop naar duur:
  1. de bronpagina's van het profiel en de theorg.com-pagina met vast patroon (gratis);
  2. pas als dat niets oplevert: een korte zoekactie van de agent naar persoonspagina's (`agent.search_photo_pages`);
  3. het fotozoeken op die extra pagina's.

  Gaat er iets mis, dan wordt het profiel gewoon zonder foto opgeslagen.

**Foutafhandeling.** Een mislukte zoekactie geeft een duidelijke foutmelding (HTTP 502), en de bestaande gegevens blijven ongewijzigd.

---

## agent.py – de zoekagent

**Wat het doet.** Stuurt Claude een opdracht met de **web search-tool**. Claude zoekt zelf op internet en levert het resultaat aan via een eigen tool, `rapporteer`. Die tool heeft een vast schema (`strict: true`), zodat het antwoord altijd dezelfde, geldige structuur heeft.

**Drie zoekacties**

| Functie | Levert op | Max. zoekopdrachten |
|---|---|---|
| `search_profile(stakeholder)` | `{"profiel": {...}, "toelichting": "..."}` | 20 |
| `search_updates(stakeholder)` | `{"updates": [...], "toelichting": "..."}` | 15 |
| `search_photo_pages(stakeholder, profiel)` | lijst met URL's van persoonspagina's | 12 |

De limieten zijn bewust ruim. Claude voert vaak meerdere zoekopdrachten tegelijk uit, en elke opdracht telt mee. Met een limiet van 5 was het budget in één keer op en vond de agent soms niets.

**De prompts**

- `BASE_RULES` bevat de regels voor alle zoekacties:
  - alleen openbare bronnen en alleen zakelijke informatie;
  - nooit privégegevens, zoals gezondheid, familie, adres of politieke voorkeur;
  - niets verzinnen, en elk item krijgt een bron;
  - twijfel over de identiteit van de persoon (een mogelijke naamgenoot) uitleggen in `toelichting`.
- `PROFILE_SYSTEM`, `UPDATES_SYSTEM` en `PHOTO_SYSTEM` voegen daar de specifieke taak aan toe. Updates zijn bijvoorbeeld beperkt tot de laatste 12 maanden, terwijl het profiel geen tijdsgrens heeft.

**De zoeklus: `_run(...)`**

1. Roept de Messages API aan (streaming) met de web search-tool en de `rapporteer`-tool.
2. Bij `pause_turn` (Claude is nog bezig) gaat de lus door, tot maximaal `MAX_CONTINUATIONS` keer.
3. Roept Claude `rapporteer` aan, dan is dat het resultaat.
4. Stopt Claude zonder `rapporteer`, dan volgt één herinnering. Komt er dan JSON in gewone tekst terug, dan wordt die alsnog uitgelezen met `parse_json`.
5. Lukt het nog steeds niet, of weigert Claude de opdracht, dan volgt een duidelijke fout.

**Opschonen**

- `clean_profile(p)`: verwijdert profielonderdelen zonder geldige `http(s)`-bron, zorgt dat alle lijsten bestaan en houdt alleen een geldige `foto_url` over.
- `clean_updates(items)`: hetzelfde voor updates. Een onbekend type wordt `overig`.

**Instellen**

| Instelling | Waar |
|---|---|
| Model | `CLAUDE_MODEL` in `.env`. Standaard is `claude-opus-5-5`. |
| Zoeklimieten | `PROFILE_MAX_SEARCHES`, `UPDATES_MAX_SEARCHES`, `PHOTO_MAX_SEARCHES` |
| Updatetypes | `UPDATE_TYPES` |

---

## storage.py – opslag

**Wat het doet.** Bewaart alle stakeholders in `data/stakeholders.json`. Dat bestand staat in `.gitignore` en komt dus niet in versiebeheer. De klasse `Storage` is het enige onderdeel dat dit bestand aanraakt.

**Per stakeholder wordt opgeslagen:**

- naam, functie, organisatie en zoekfocus (wat je zelf invult);
- `profiel`, `profiel_bijgewerkt` en `profiel_toelichting`;
- `updates` (titel, type, samenvatting, bron, publicatiedatum, ophaalmoment), `laatst_gezocht` en `updates_toelichting`.

**Methoden**

| Methode | Doel |
|---|---|
| `list()` / `get(id)` | Ophalen. Ruimt eerst oude updates op en vult velden aan die in oudere records ontbreken. |
| `add(...)` / `delete(id)` | Toevoegen en verwijderen. |
| `save_profile(id, profiel, toelichting)` | Vervangt het hele profiel en zet de tijd van bijwerken. |
| `set_photo(id, foto_url)` | Past alleen de foto aan en laat de rest van het profiel staan. |
| `add_updates(id, updates, toelichting)` | Voegt alleen nieuwe updates toe. Ontdubbeld wordt op bron-URL, en de functie geeft terug welke updates nieuw waren. |

**Gedrag dat goed is om te weten**

- **Bewaartermijn:** updates worden 365 dagen na het ophalen automatisch verwijderd (`RETENTION_DAYS`). Het profiel valt hier niet onder. Dat wordt bij elke profielactie vervangen.
- **Sortering:** updates met een publicatiedatum staan eerst, de nieuwste bovenaan. Daarna volgen de updates zonder datum. Zo is de "laatste update" altijd echt nieuws.
- **Veilig schrijven:** er wordt eerst naar een tijdelijk bestand geschreven, dat daarna het echte bestand vervangt. Een vergrendeling (`_lock`) voorkomt dat twee acties tegelijk schrijven.
- **Beschadigd bestand:** is het JSON-bestand onleesbaar, dan wordt het bewaard als `stakeholders.corrupt-<tijd>.json` en begint de app met een lege lijst.

---

## photos.py – de fotozoeker

**Wat het doet.** Zoekt een officiële portretfoto zonder AI. De module opent webpagina's, zoekt daarin een afbeelding die bij de naam past, en controleert of de link echt een afbeelding oplevert.

**Hoe een foto wordt gekozen** (in `find_photo`)

1. **De foto van de agent.** Gaf de agent een `foto_url` die echt een afbeelding is, dan wordt die gebruikt.
2. **`<img>` met de naam** (`pick_image`). Op elke kandidaatpagina zoekt de module een afbeelding met de volledige naam in de alt-tekst of bestandsnaam (bijvoorbeeld `ruud-joosten-2026.jpg`). Een treffer op alleen de achternaam telt minder zwaar. Uit een `srcset` wordt de grootste versie tot 800 px gekozen.
3. **Deelafbeelding met de naam** (`pick_og_image`). Dit is de `og:image`, de afbeelding die verschijnt als je de pagina deelt. Die wordt alleen gebruikt als de naam in de afbeeldingslink staat, zodat logo's en artikelfoto's buiten de boot vallen. Sites als theorg.com stoppen het portret in een `image=`-parameter, en die wordt eruit gehaald. Van theorg-thumbnails wordt eerst de grotere `_medium`-versie geprobeerd.

**Welke pagina's worden bekeken**

- `candidate_pages`: de bronnen van de huidige functie, de online profielen en de loopbaan (maximaal 6), aangevuld met extra pagina's.
- `guessed_pages`: `theorg.com/org/<organisatie>/org-chart/<naam>`. De zoekmachine vindt die pagina vaak niet, terwijl hij wel bestaat.
- LinkedIn, Bloomberg, X, Twitter en Facebook worden overgeslagen (`SKIP_HOSTS`), omdat die automatische bezoekers blokkeren.

**Bescherming tegen naamgenoten.** Een extra pagina, die niet uit het gecontroleerde profiel komt, wordt alleen gebruikt als de organisatie op die pagina genoemd wordt (`_mentions`).

**Instellen**

| Instelling | Betekenis |
|---|---|
| `TIMEOUT` | Hoe lang er per pagina gewacht wordt (8 s). |
| `MAX_PAGES` | Hoeveel pagina's maximaal worden bekeken. |
| `SKIP_HOSTS` | Sites die worden overgeslagen. |

De foto wordt niet gedownload. De app bewaart alleen de link en de browser laadt de foto rechtstreeks. Werkt de link niet meer, dan toont de pagina automatisch de initialen.

---

## tests/ – automatische tests

Uitvoeren met:

```
.venv\Scripts\python -m pytest -q
```

De tests doen geen echte API-aanroepen en bezoeken geen websites. Ze kosten dus niets en werken offline.

| Bestand | Test |
|---|---|
| [test_core.py](../tests/test_core.py) | De basis van de opslag: toevoegen, verplichte naam, ontdubbelen, verwijderen, bewaartermijn en een beschadigd bestand. Daarnaast het uitlezen van JSON uit vrije tekst. |
| [test_profiel.py](../tests/test_profiel.py) | Het opschonen van profiel en updates. De zoeklus met een nep-client: het tool-resultaat, `pause_turn`, de herinnering, de terugval op tekst en een weigering. Verder de opslag van profiel en foto, de sortering van updates en oude records zonder nieuwe velden. |
| [test_photos.py](../tests/test_photos.py) | Een foto kiezen uit `<img>` en `srcset`, deelafbeeldingen (zoals theorg), het negeren van logo's, het overslaan van LinkedIn, het vaste theorg-patroon, de organisatiecontrole tegen naamgenoten en de terugval naar de thumbnail. |
