import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agent import clean_updates, parse_json
from storage import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "s.json")


def test_add_and_list(store):
    s = store.add("Jan Jansen", "Directeur", "Acme")
    assert store.list()[0]["id"] == s["id"]


def test_add_requires_name(store):
    with pytest.raises(ValueError):
        store.add("  ")


def test_dedupe_on_url(store):
    s = store.add("Jan")
    u = {"titel": "t", "samenvatting": "x", "bron_url": "https://a.nl/1"}
    assert len(store.add_updates(s["id"], [u])) == 1
    assert store.add_updates(s["id"], [u]) == []
    assert len(store.get(s["id"])["updates"]) == 1


def test_update_without_url_dropped(store):
    s = store.add("Jan")
    assert store.add_updates(s["id"], [{"titel": "t", "bron_url": ""}]) == []


def test_delete(store):
    s = store.add("Jan")
    assert store.delete(s["id"]) is True
    assert store.delete(s["id"]) is False
    assert store.list() == []


def test_purge_old_updates(tmp_path):
    store = Storage(tmp_path / "s.json", retention_days=30)
    s = store.add("Jan")
    store.add_updates(s["id"], [{"titel": "oud", "bron_url": "https://a.nl/1"}])
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["stakeholders"][0]["updates"][0]["opgehaald"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    assert store.list()[0]["updates"] == []


def test_corrupt_file_is_backed_up(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{kapot", encoding="utf-8")
    store = Storage(p)
    assert store.list() == []
    assert list(tmp_path.glob("s.corrupt-*.json"))


def test_parse_json_ok_and_clean_drops_missing_source():
    text = 'Hier: {"updates": [{"titel":"A","samenvatting":"s","bron_url":"https://x.nl","gepubliceerd":"2026-01-01"},{"titel":"B","bron_url":""}]}'
    res = clean_updates(parse_json(text)["updates"])
    assert len(res) == 1 and res[0]["titel"] == "A"


def test_parse_json_no_json():
    with pytest.raises(ValueError):
        parse_json("geen json")
