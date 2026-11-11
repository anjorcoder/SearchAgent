"""JSON-opslag voor stakeholders en hun updates."""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

RETENTION_DAYS = 365
_lock = threading.RLock()


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class Storage:
    def __init__(self, path, retention_days=RETENTION_DAYS):
        self.path = Path(path)
        self.retention_days = retention_days
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- low level -------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return {"stakeholders": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("stakeholders"), list):
                raise ValueError("onverwachte structuur")
            return data
        except (json.JSONDecodeError, ValueError):
            backup = self.path.with_suffix(f".corrupt-{_now():%Y%m%d%H%M%S}.json")
            os.replace(self.path, backup)
            return {"stakeholders": []}

    def _save(self, data):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _purge(self, data):
        cutoff = _now() - timedelta(days=self.retention_days)
        for s in data["stakeholders"]:
            kept = []
            for u in s.get("updates", []):
                try:
                    fetched = datetime.fromisoformat(u["opgehaald"])
                except (KeyError, ValueError):
                    fetched = _now()
                if fetched >= cutoff:
                    kept.append(u)
            s["updates"] = kept

    @staticmethod
    def _normalize(data):
        """Vult velden aan die in oudere records ontbreken en sorteert updates nieuwste eerst."""
        for s in data["stakeholders"]:
            s.setdefault("profiel", None)
            s.setdefault("profiel_bijgewerkt", None)
            s.setdefault("profiel_toelichting", "")
            s.setdefault("updates_toelichting", "")
            for u in s.setdefault("updates", []):
                u.setdefault("type", "overig")
            # Gedateerde updates eerst (nieuwste boven), daarna ongedateerde op ophaalmoment.
            s["updates"].sort(
                key=lambda u: (bool(u.get("gepubliceerd")), u.get("gepubliceerd") or "", u.get("opgehaald", "")),
                reverse=True,
            )

    # -- public ----------------------------------------------------------
    def list(self):
        with _lock:
            data = self._load()
            before = json.dumps(data, sort_keys=True)
            self._purge(data)
            if json.dumps(data, sort_keys=True) != before:
                self._save(data)
            self._normalize(data)
            return data["stakeholders"]

    def get(self, stakeholder_id):
        for s in self.list():
            if s["id"] == stakeholder_id:
                return s
        return None

    def add(self, naam, functie="", organisatie="", zoekfocus=""):
        naam = (naam or "").strip()
        if not naam:
            raise ValueError("Naam is verplicht")
        with _lock:
            data = self._load()
            s = {
                "id": str(uuid.uuid4()),
                "naam": naam,
                "functie": (functie or "").strip(),
                "organisatie": (organisatie or "").strip(),
                "zoekfocus": (zoekfocus or "").strip(),
                "laatst_gezocht": None,
                "updates": [],
                "updates_toelichting": "",
                "profiel": None,
                "profiel_bijgewerkt": None,
                "profiel_toelichting": "",
            }
            data["stakeholders"].append(s)
            self._save(data)
            return s

    def delete(self, stakeholder_id):
        with _lock:
            data = self._load()
            n = len(data["stakeholders"])
            data["stakeholders"] = [s for s in data["stakeholders"] if s["id"] != stakeholder_id]
            if len(data["stakeholders"]) == n:
                return False
            self._save(data)
            return True

    def _find(self, data, stakeholder_id):
        s = next((x for x in data["stakeholders"] if x["id"] == stakeholder_id), None)
        if s is None:
            raise KeyError(stakeholder_id)
        return s

    def save_profile(self, stakeholder_id, profiel, toelichting=""):
        """Vervangt het profiel van een stakeholder."""
        with _lock:
            data = self._load()
            s = self._find(data, stakeholder_id)
            s["profiel"] = profiel
            s["profiel_toelichting"] = (toelichting or "").strip()
            s["profiel_bijgewerkt"] = _iso(_now())
            self._save(data)

    def set_photo(self, stakeholder_id, foto_url):
        """Zet alleen de foto van een bestaand profiel, zonder de rest te vervangen."""
        with _lock:
            data = self._load()
            s = self._find(data, stakeholder_id)
            if not s.get("profiel"):
                raise ValueError("Er is nog geen profiel")
            s["profiel"]["foto_url"] = foto_url
            self._save(data)

    def add_updates(self, stakeholder_id, updates, toelichting=""):
        """Voegt nieuwe updates toe (dedupliceert op bron_url). Geeft de nieuwe terug."""
        with _lock:
            data = self._load()
            s = self._find(data, stakeholder_id)
            s["updates_toelichting"] = (toelichting or "").strip()
            known = {u["bron_url"] for u in s["updates"]}
            nieuw = []
            now = _iso(_now())
            for u in updates:
                url = (u.get("bron_url") or "").strip()
                if not url or url in known:
                    continue
                known.add(url)
                rec = {
                    "titel": (u.get("titel") or "").strip(),
                    "type": u.get("type") or "overig",
                    "samenvatting": (u.get("samenvatting") or "").strip(),
                    "bron_url": url,
                    "gepubliceerd": u.get("gepubliceerd") or None,
                    "opgehaald": now,
                }
                nieuw.append(rec)
            s["updates"] = nieuw + s["updates"]
            s["laatst_gezocht"] = now
            self._save(data)
            return nieuw
