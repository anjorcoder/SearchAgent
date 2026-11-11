# Profielpagina per stakeholder – ontwerp

Datum: 2026-10-29
Bouwt voort op: `2026-10-22-stakeholder-monitor-design.md`

## Doel

Bij een opgeslagen stakeholder wil de gebruiker een profielpagina zien. Die bevat alle openbare, **zakelijke** informatie over de persoon en een uitgelichte laatste update, met daaronder een tijdlijn van eerdere updates.

## Aanleiding: "vindt niks"

Een zoekactie op Ruud Joosten (CEO, BAM, focus "projecten") gaf in de app 0 updates. Los uitgevoerd gaf dezelfde vraag 5 tot 7 updates. De oorzaak is geen parse-bug maar wisselend modelgedrag:

- Het prompt is streng ("bij twijfel weglaten", alleen de laatste 12 maanden, zoekfocus als filter). Daardoor levert het model soms `{"updates": []}`.
- Een lege lijst is in de app niet te onderscheiden van "er bestaat niets", want het model kan niet uitleggen waarom het niets vond.
- De JSON wordt met een regex uit vrije tekst gehaald. Dat is foutgevoelig.

## Keuzes (afgestemd met gebruiker)

| Vraag | Keuze |
|---|---|
| Inhoud profiel | Alleen zakelijk: geen privé-informatie (AVG-kader blijft ongewijzigd) |
| Weergave updates | Uitgelichte laatste update + tijdlijn |
| Flow | Persoon opslaan, daarna profiel opbouwen. Er is geen snelzoeken zonder opslaan |
| Zoekacties | **Twee aparte acties**: "Profiel opbouwen" (af en toe) en "Zoek updates" (vaak) |

## Architectuur

### agent.py

Twee publieke functies op één gedeelde zoeklus:

- `search_profile(stakeholder, client=None) -> {"profiel": {...}, "toelichting": str}`
- `search_updates(stakeholder, client=None) -> {"updates": [...], "toelichting": str}`. **Let op:** de functie gaf eerst een lijst terug. Nu geeft ze een dict terug, en `app.py` past zich daarop aan.

De gedeelde privéfunctie `_run(system, prompt, report_tool, client)` doet het volgende:

1. Ze roept de Messages API aan (streaming, zoals nu) met de `web_search`-tool en een eigen tool `rapporteer`. Die tool heeft een JSON-schema met `strict: true`, en elke actie heeft een eigen schema.
2. Bij `pause_turn` gaat ze door, zoals nu, tot `MAX_CONTINUATIONS`.
3. Roept het model `rapporteer` aan, dan is de `input` van die tool-aanroep het resultaat.
4. Eindigt het model zonder `rapporteer` (`end_turn`), dan stuurt ze één vervolgbericht: "Lever je resultaat nu aan via de tool rapporteer." Een eventueel JSON-antwoord in tekst vangt ze daarbij op met de bestaande regex-parser als terugvaloptie. Lukt het dan nog niet, dan geeft ze een `RuntimeError` met een duidelijke melding.
5. Een `refusal` geeft een `RuntimeError`, zoals nu.

`tool_choice` blijft `auto`, omdat een geforceerde tool het zoeken zou overslaan. Tijdens de implementatie wordt geverifieerd of het model met ingeschakeld denken (thinking) `rapporteer` betrouwbaar aanroept.

**Gedeelde systeemregels** (beide prompts): de bestaande privacyregels, alleen openbare bronnen, niets verzinnen, elke bewering met een bron_url, en naamgenoten uitsluiten. Twee aanpassingen:

- Twijfel over identiteit → weglaten **en** dat uitleggen in `toelichting`. Dat geldt ook als functie of organisatie niet klopt, bijvoorbeeld: "Ruud Joosten is CEO van Koninklijke BAM Groep, niet van BAM Nederland."
- De zoekfocus is een voorkeur, geen filter.

**Profielschema** (`rapporteer` bij een profielactie):

```json
{
  "profiel": {
    "samenvatting": "2-4 zinnen, zakelijk",
    "huidige_functie": {"functie": "...", "organisatie": "...", "sinds": "YYYY(-MM) of null", "bron_url": "https://..."},
    "loopbaan":       [{"functie": "...", "organisatie": "...", "periode": "...", "bron_url": "https://..."}],
    "opleiding":      [{"opleiding": "...", "instelling": "...", "periode": "... of null", "bron_url": "https://..."}],
    "nevenfuncties":  [{"rol": "...", "organisatie": "...", "periode": "... of null", "bron_url": "https://..."}],
    "thema_s":        [{"thema": "...", "toelichting": "1 zin", "bron_url": "https://..."}],
    "profielen":      [{"label": "Bedrijfsprofiel / LinkedIn / ...", "url": "https://..."}]
  },
  "toelichting": "korte uitleg, leeg als er niets bijzonders is"
}
```

Voor het profiel geldt geen grens van 12 maanden. Na afloop gaat het profiel door een opschoonfunctie `clean_profile()`. Die verwijdert items zonder geldige http(s)-URL, maakt strings schoon en vult ontbrekende lijsten aan met `[]`.

**Updateschema** (`rapporteer` bij een updateactie):

```json
{
  "updates": [{"titel": "...", "type": "benoeming|project|interview|persbericht|publicatie|toespraak|bedrijfsnieuws|overig",
               "samenvatting": "1-2 zinnen", "bron_url": "https://...", "gepubliceerd": "YYYY-MM-DD of null"}],
  "toelichting": "..."
}
```

Updates blijven beperkt tot de afgelopen 12 maanden. Dat is de bewaartermijn, en die blijft ongewijzigd. `parse_updates` wordt omgebouwd tot `clean_updates(items)`, met dezelfde regels als nu plus het veld `type`. Een onbekend type wordt `overig`.

### storage.py

Er komen nieuwe velden per stakeholder bij. Bestaande records zonder deze velden blijven werken, omdat ontbrekende velden als leeg gelden.

- `profiel`: dict of `null`
- `profiel_bijgewerkt`: ISO-tijd of `null`
- `profiel_toelichting`: string
- `updates_toelichting`: string

Daarnaast:

- Nieuw: `save_profile(id, profiel, toelichting)` overschrijft het profiel en zet `profiel_bijgewerkt`.
- `add_updates(id, updates, toelichting="")` slaat ook `type` en `updates_toelichting` op.
- `list()` / `get()` sorteren de updates per stakeholder: eerst de gedateerde updates, aflopend op `gepubliceerd`. Daarna de ongedateerde, aflopend op `opgehaald`. Zo is `updates[0]` de nieuwste gedateerde update. Een ongedateerd item dat vandaag is opgehaald, verdringt geen recent nieuws.

De profieldata valt niet onder de purge van 12 maanden. Ze wordt bij elke profielactie vervangen en verdwijnt bij het verwijderen van de stakeholder.

### app.py

- Nieuw: `POST /api/stakeholders/<id>/profile` roept `agent.search_profile` aan en daarna `store.save_profile`. Het endpoint geeft `{"stakeholder": ...}` terug.
- Bestaand: `POST /api/stakeholders/<id>/search` geeft `{"nieuw": [...], "toelichting": "...", "stakeholder": ...}` terug.
- De foutafhandeling blijft zoals nu: 404, 500 bij een ontbrekende key, 502 bij een agentfout. Bij een fout blijven de bestaande data intact.

### static/index.html

Er komt een hash-routing in hetzelfde bestand.

- `#/` (lijst): het formulier zoals nu. Per persoon een compacte kaart met naam, functie · organisatie, de titel en datum van de laatste update, en de knoppen "Open profiel" en "Verwijderen".
- `#/p/<id>` (profielpagina), van boven naar beneden:
  1. Kop met naam, functie · organisatie en een terug-link.
  2. Twee knoppen, elk met een eigen "laatst bijgewerkt / laatst gezocht":
     - **Profiel opbouwen**
     - **Zoek updates**

     Elke knop heeft een eigen bezig-status en foutmelding.
  3. **Laatste update**, uitgelicht: `updates[0]`, met type-label, datum, samenvatting en bron.
  4. **Toelichting**, als er een is (van profiel of updates), als subtiele melding.
  5. **Profiel**, in blokken: samenvatting, huidige functie, loopbaan, opleiding, nevenfuncties, thema's en profielen. Lege blokken worden verborgen. Elk item linkt naar zijn bron. Is er nog geen profiel, dan staat er "Nog geen profiel. Klik op Profiel opbouwen."
  6. **Tijdlijn**: de overige updates, het nieuwste eerst. Nieuw gevonden updates worden gemarkeerd, zoals nu.

Alle links gaan via `safeHref`, en de tekst via `textContent` (zoals nu, zonder innerHTML met data).

## Foutafhandeling

| Situatie | Gedrag |
|---|---|
| Agent levert geen `rapporteer` aan, ook niet na het vervolgbericht | 502 "De agent gaf geen bruikbaar resultaat; probeer het opnieuw." |
| Profiel leeg of alleen een toelichting | Opslaan; de pagina toont de toelichting |
| 0 updates met toelichting | Toelichting tonen; bestaande updates blijven staan |
| Agentfout tijdens het profiel | Het oude profiel blijft staan |

## Testen (pytest, zonder echte API-aanroepen)

- `clean_profile`: verwijdert items zonder URL, vult ontbrekende lijsten aan, en houdt geldige items over.
- `clean_updates`: werkt als de bestaande test en zet een onbekend type om naar `overig`.
- `_run` met een nep-client:
  - een `rapporteer`-aanroep geeft de input terug
  - `end_turn` zonder tool gevolgd door een tool-aanroep na het vervolgbericht levert een resultaat op
  - twee keer niets geeft een `RuntimeError`
  - `pause_turn` gaat door
- Storage:
  - `save_profile` overschrijft en zet een tijdstempel
  - updates worden gesorteerd (met en zonder `gepubliceerd`)
  - oude records zonder de nieuwe velden laden correct
- Handmatig: Ruud Joosten met beide knoppen. Het profiel moet gevuld zijn, er moet minstens één update zijn, en de toelichting moet zichtbaar zijn als er een is.

## Buiten scope

Snelzoeken zonder opslaan, automatisch periodiek zoeken, export, en privé of persoonlijke informatie.
