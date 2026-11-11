import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import agent
from agent import clean_profile, clean_updates
from storage import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "s.json")


# -- clean_* ---------------------------------------------------------------
def test_clean_updates_unknown_type_becomes_overig():
    res = clean_updates(
        [
            {"titel": "A", "type": "roddel", "bron_url": "https://x.nl", "gepubliceerd": "2026-01-01"},
            {"titel": "B", "type": "interview", "bron_url": "https://y.nl"},
            {"titel": "C", "bron_url": "ftp://z"},
        ]
    )
    assert [u["type"] for u in res] == ["overig", "interview"]


def test_clean_profile_drops_items_without_url_and_fills_lists():
    p = clean_profile(
        {
            "samenvatting": " CEO van X. ",
            "huidige_functie": {"functie": "CEO", "organisatie": "X", "sinds": None, "bron_url": "https://x.nl"},
            "loopbaan": [
                {"functie": "CFO", "organisatie": "Y", "periode": "2010-2015", "bron_url": "https://y.nl"},
                {"functie": "Geheim", "organisatie": "Z", "periode": "", "bron_url": ""},
            ],
            "profielen": [{"label": "LinkedIn", "url": "javascript:alert(1)"}],
        }
    )
    assert p["samenvatting"] == "CEO van X."
    assert len(p["loopbaan"]) == 1
    assert p["opleiding"] == [] and p["nevenfuncties"] == [] and p["thema_s"] == []
    assert p["profielen"] == []
    assert p["huidige_functie"]["functie"] == "CEO"


def test_clean_profile_foto_and_kernpunten():
    p = clean_profile({"foto_url": "data:image/png;base64,xx", "kernpunten": [" a ", "", 3, "b"]})
    assert p["foto_url"] is None
    assert p["kernpunten"] == ["a", "b"]
    assert clean_profile({"foto_url": "https://x.nl/f.jpg"})["foto_url"] == "https://x.nl/f.jpg"


def test_clean_profile_huidige_functie_without_url_is_none():
    p = clean_profile({"huidige_functie": {"functie": "CEO", "organisatie": "X", "bron_url": ""}})
    assert p["huidige_functie"] is None


# -- _run met nep-client ---------------------------------------------------
def _msg(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def _tool(inp):
    return SimpleNamespace(type="tool_use", name="rapporteer", id="t1", input=inp)


def _text(t):
    return SimpleNamespace(type="text", text=t)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)

        class Ctx:
            def __enter__(s):
                return SimpleNamespace(get_final_message=lambda: reply)

            def __exit__(s, *a):
                return False

        return Ctx()


TOOL = {"name": "rapporteer", "input_schema": {"type": "object"}}


def test_run_returns_tool_input():
    c = FakeClient([_msg("tool_use", _tool({"updates": [], "toelichting": "x"}))])
    assert agent._run("sys", "prompt", TOOL, 5, c) == {"updates": [], "toelichting": "x"}


def test_run_continues_on_pause_turn():
    c = FakeClient([_msg("pause_turn", _text("...")), _msg("tool_use", _tool({"a": 1}))])
    assert agent._run("sys", "prompt", TOOL, 5, c) == {"a": 1}
    assert len(c.calls) == 2


def test_run_nudges_once_when_no_tool_call():
    c = FakeClient([_msg("end_turn", _text("klaar")), _msg("tool_use", _tool({"a": 1}))])
    assert agent._run("sys", "prompt", TOOL, 5, c) == {"a": 1}
    assert "rapporteer" in c.calls[1]["messages"][-1]["content"]


def test_run_falls_back_to_json_text():
    c = FakeClient([_msg("end_turn", _text("x")), _msg("end_turn", _text('{"a": 2}'))])
    assert agent._run("sys", "prompt", TOOL, 5, c) == {"a": 2}


def test_run_raises_when_nothing_usable():
    c = FakeClient([_msg("end_turn", _text("x")), _msg("end_turn", _text("nog steeds niets"))])
    with pytest.raises(RuntimeError):
        agent._run("sys", "prompt", TOOL, 5, c)


def test_run_raises_on_refusal():
    c = FakeClient([_msg("refusal")])
    with pytest.raises(RuntimeError):
        agent._run("sys", "prompt", TOOL, 5, c)


# -- storage ---------------------------------------------------------------
def test_save_profile_overwrites_and_timestamps(store):
    s = store.add("Jan")
    store.save_profile(s["id"], {"samenvatting": "a"}, "t1")
    store.save_profile(s["id"], {"samenvatting": "b"}, "")
    got = store.get(s["id"])
    assert got["profiel"] == {"samenvatting": "b"}
    assert got["profiel_toelichting"] == ""
    assert got["profiel_bijgewerkt"]


def test_set_photo_keeps_rest_of_profile(store):
    s = store.add("Jan")
    with pytest.raises(ValueError):
        store.set_photo(s["id"], "https://x.nl/f.jpg")
    store.save_profile(s["id"], {"samenvatting": "a", "foto_url": None}, "t")
    store.set_photo(s["id"], "https://x.nl/f.jpg")
    got = store.get(s["id"])
    assert got["profiel"] == {"samenvatting": "a", "foto_url": "https://x.nl/f.jpg"}
    assert got["profiel_toelichting"] == "t"


def test_save_profile_unknown_id(store):
    with pytest.raises(KeyError):
        store.save_profile("nope", {}, "")


def test_updates_sorted_newest_first(store):
    s = store.add("Jan")
    store.add_updates(
        s["id"],
        [
            {"titel": "oud", "bron_url": "https://a.nl/1", "gepubliceerd": "2026-01-01"},
            {"titel": "nieuw", "bron_url": "https://a.nl/2", "gepubliceerd": "2026-09-01"},
            {"titel": "geen datum", "bron_url": "https://a.nl/3"},
        ],
        "toel",
    )
    got = store.get(s["id"])
    assert [u["titel"] for u in got["updates"]][:2] == ["nieuw", "oud"]
    assert got["updates_toelichting"] == "toel"
    assert got["updates"][0]["type"] == "overig"


def test_old_records_without_new_fields_load(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(
        json.dumps({"stakeholders": [{"id": "1", "naam": "Jan", "laatst_gezocht": None, "updates": []}]}),
        encoding="utf-8",
    )
    s = Storage(p).get("1")
    assert s["profiel"] is None and s["profiel_bijgewerkt"] is None
    assert s["profiel_toelichting"] == "" and s["updates_toelichting"] == ""
