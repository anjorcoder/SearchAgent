"""Stakeholder Monitor – lokale Flask-server (alleen 127.0.0.1)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

import agent  # noqa: E402  (na load_dotenv zodat CLAUDE_MODEL uit .env geldt)
import photos  # noqa: E402
from storage import Storage  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
store = Storage(BASE / "data" / "stakeholders.json")


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/stakeholders")
def list_stakeholders():
    return jsonify(store.list())


@app.post("/api/stakeholders")
def add_stakeholder():
    body = request.get_json(silent=True) or {}
    try:
        s = store.add(
            body.get("naam"), body.get("functie"), body.get("organisatie"), body.get("zoekfocus")
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(s), 201


@app.delete("/api/stakeholders/<stakeholder_id>")
def delete_stakeholder(stakeholder_id):
    if not store.delete(stakeholder_id):
        return jsonify({"error": "Niet gevonden"}), 404
    return "", 204


def _precheck(stakeholder_id):
    s = store.get(stakeholder_id)
    if s is None:
        return None, (jsonify({"error": "Niet gevonden"}), 404)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, (jsonify({"error": "ANTHROPIC_API_KEY ontbreekt. Zet hem in het .env-bestand."}), 500)
    return s, None


def _find_photo(s, profiel):
    """Eerst op de bronpagina's van het profiel; lukt dat niet, dan gericht persoonspagina's zoeken."""
    org = s.get("organisatie", "")
    try:
        guesses = photos.guessed_pages(s["naam"], org)
        foto = photos.find_photo(s["naam"], profiel, extra_pages=guesses, organisatie=org)
        if not foto:
            pages = agent.search_photo_pages(s, profiel)
            foto = photos.find_photo(s["naam"], profiel, extra_pages=pages, organisatie=org)
        return foto
    except Exception:  # een foto is bijzaak; het profiel gaat voor
        return None


@app.post("/api/stakeholders/<stakeholder_id>/photo")
def photo(stakeholder_id):
    s, err = _precheck(stakeholder_id)
    if err:
        return err
    if not s.get("profiel"):
        return jsonify({"error": "Bouw eerst het profiel op."}), 400
    foto = _find_photo(s, s["profiel"])
    if foto:
        store.set_photo(stakeholder_id, foto)
    return jsonify({"gevonden": bool(foto), "stakeholder": store.get(stakeholder_id)})


@app.post("/api/stakeholders/<stakeholder_id>/search")
def search(stakeholder_id):
    s, err = _precheck(stakeholder_id)
    if err:
        return err
    try:
        res = agent.search_updates(s)
        nieuw = store.add_updates(stakeholder_id, res["updates"], res["toelichting"])
    except Exception as e:  # bestaande data blijft intact
        return jsonify({"error": f"Zoeken mislukt: {e}"}), 502
    return jsonify({"nieuw": nieuw, "toelichting": res["toelichting"], "stakeholder": store.get(stakeholder_id)})


@app.post("/api/stakeholders/<stakeholder_id>/profile")
def profile(stakeholder_id):
    s, err = _precheck(stakeholder_id)
    if err:
        return err
    try:
        res = agent.search_profile(s)
        res["profiel"]["foto_url"] = _find_photo(s, res["profiel"])
        store.save_profile(stakeholder_id, res["profiel"], res["toelichting"])
    except Exception as e:  # oud profiel blijft intact
        return jsonify({"error": f"Profiel opbouwen mislukt: {e}"}), 502
    return jsonify({"stakeholder": store.get(stakeholder_id)})


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WAARSCHUWING: ANTHROPIC_API_KEY niet gevonden (.env). Zoeken werkt pas na het instellen.")
    app.run(host="127.0.0.1", port=5000, debug=False)
