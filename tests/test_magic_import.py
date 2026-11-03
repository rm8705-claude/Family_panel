"""Magic Import: extraction pipeline (LLM stubbed), defensive parsing,
review approve/dismiss flow, the never-auto-approve guarantee, and the
IMAP poll (poison-message retry cap)."""
import email.message
import json
import os
from datetime import timedelta

import pytest

import db
import magic_import

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def future(days: int) -> str:
    return (db.local_today() + timedelta(days=days)).isoformat()


def stub_llm(monkeypatch, payload: str):
    monkeypatch.setattr(magic_import, "call_llm", lambda prompt: payload)


# ------------------------------------------------------------------ IMAP fakes

class FakeMailbox:
    """The persistent server-side state a real IMAP server would hold —
    separate from the connection object, since poll_inbox() opens a fresh
    `with imaplib.IMAP4_SSL(...)` connection on every call but the mailbox's
    messages/flags must survive across those successive polls."""

    def __init__(self):
        self.messages: dict[bytes, bytes] = {}
        self.seen: set[bytes] = set()

    def add(self, num: bytes, raw: bytes) -> None:
        self.messages[num] = raw


def raw_email(subject: str, from_addr: str, body: str, message_id: str) -> bytes:
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["Message-ID"] = message_id
    msg.set_content(body)
    return bytes(msg)


class FakeIMAPConnection:
    """Enough of imaplib.IMAP4_SSL's surface for poll_inbox: login/select/
    search/fetch/store, plus the context-manager protocol."""

    def __init__(self, mailbox: FakeMailbox):
        self.mailbox = mailbox

    def login(self, user, password):
        return "OK", [b"logged in"]

    def select(self, mailbox):
        return "OK", [b"1"]

    def search(self, charset, criterion):
        assert criterion == "UNSEEN"
        unseen = sorted(n for n in self.mailbox.messages if n not in self.mailbox.seen)
        return "OK", [b" ".join(unseen)]

    def fetch(self, num, spec):
        raw = self.mailbox.messages.get(num)
        if raw is None:
            return "NO", [None]
        return "OK", [(b"1 (BODY[])", raw)]

    def store(self, num, flags, value):
        if value == "\\Seen":
            self.mailbox.seen.add(num)
        return "OK", [b"done"]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def install_fake_imap(monkeypatch, mailbox: FakeMailbox):
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("IMAP_USER", "inbox@example.test")
    monkeypatch.setenv("IMAP_APP_PASSWORD", "app-password")
    monkeypatch.setattr(magic_import.imaplib, "IMAP4_SSL",
                        lambda host, timeout=None: FakeIMAPConnection(mailbox))


def test_school_email_produces_pending_rows(client, monkeypatch):
    stub_llm(monkeypatch, json.dumps({"events": [
        {"title": "Pupil free day", "date": future(10), "start_time": None,
         "end_time": None, "all_day": True, "person_hint": "kid",
         "location": None, "notes": "No school for students"},
        {"title": "Queensland Museum excursion", "date": future(19),
         "start_time": "09:15", "end_time": "14:30", "all_day": False,
         "person_hint": "kid", "location": "Queensland Museum", "notes": None},
    ]}))
    n = magic_import.process_message(
        "Week 3 newsletter", "newsletter@school.example",
        fixture("school_email.txt"))
    assert n == 2

    pending = client.get("/api/imports/pending").get_json()["imports"]
    assert len(pending) == 1
    events = pending[0]["events"]
    assert events[0]["title"] == "Pupil free day" and events[0]["all_day"] is True
    assert events[1]["start_time"] == "09:15"

    # Nothing was auto-approved: no local events yet
    agenda = client.get(f"/api/agenda?from={future(0)}&to={future(30)}").get_json()
    assert agenda["events"] == []


def test_no_events_email_zero_rows_no_crash(client, monkeypatch):
    stub_llm(monkeypatch, '{"events": []}')
    n = magic_import.process_message(
        "Your order has shipped!", "noreply@store.example",
        fixture("no_events_email.txt"))
    assert n == 0
    assert client.get("/api/imports/pending").get_json()["imports"] == []


def test_defensive_parsing():
    fenced = "```json\n{\"events\": [{\"title\": \"X\", \"date\": \"%s\"}]}\n```" % future(5)
    events = magic_import.parse_llm_json(fenced)
    assert events[0]["title"] == "X" and events[0]["all_day"] is True

    with_prose = 'Here you go:\n{"events": []}\nHope that helps!'
    assert magic_import.parse_llm_json(with_prose) == []

    with pytest.raises(ValueError):
        magic_import.parse_llm_json("I could not find any events, sorry.")

    # stale (>7 days past), bad dates and junk entries are dropped
    junk = json.dumps({"events": [
        {"title": "Old", "date": (db.local_today() - timedelta(days=30)).isoformat()},
        {"title": "Bad date", "date": "next Tuesday"},
        {"title": "", "date": future(3)},
        "not a dict",
        {"title": "Keeper", "date": future(3), "start_time": "25:99"},
    ]})
    events = magic_import.parse_llm_json(junk)
    assert [e["title"] for e in events] == ["Keeper"]
    assert events[0]["all_day"] is True   # invalid time degraded to all-day


def test_pdf_flyer_text_extraction():
    with open(os.path.join(FIXTURES, "school_flyer.pdf"), "rb") as f:
        text = magic_import.pdf_text(f.read())
    assert "Book Week Parade" in text
    assert "Swimming Carnival" in text


def test_approve_flow_with_person_picker(client, monkeypatch):
    stub_llm(monkeypatch, json.dumps({"events": [
        {"title": "Book week parade", "date": future(7), "start_time": "09:00",
         "end_time": None, "all_day": False, "person_hint": "kid",
         "location": "School hall", "notes": None},
        {"title": "P&C meeting", "date": future(9), "start_time": "19:00",
         "end_time": "20:00", "all_day": False, "person_hint": None,
         "location": None, "notes": None},
    ]}))
    magic_import.process_message("Flyer", "school@example.com", "body")
    imp = client.get("/api/imports/pending").get_json()["imports"][0]

    # approve only event 0, overriding the person; event 1 is dropped
    r = client.post(f"/api/imports/{imp['id']}/approve", json={
        "events": [{"index": 0, "person_id": "family"}]}).get_json()
    assert r["ok"] and len(r["created"]) == 1
    ev = r["created"][0]
    assert ev["person_id"] == "family" and ev["source"] == "local"

    assert client.get("/api/imports/pending").get_json()["imports"] == []
    # double-approve is rejected
    assert client.post(f"/api/imports/{imp['id']}/approve",
                       json={"events": []}).status_code == 404

    agenda = client.get(f"/api/agenda?from={future(0)}&to={future(30)}").get_json()
    assert [e["title"] for e in agenda["events"]] == ["Book week parade"]


def test_dismiss_flow(client, monkeypatch):
    stub_llm(monkeypatch, json.dumps({"events": [
        {"title": "Sale event", "date": future(2), "all_day": True}]}))
    magic_import.process_message("Spam-ish", "shop@example.com", "body")
    imp = client.get("/api/imports/pending").get_json()["imports"][0]
    assert client.post(f"/api/imports/{imp['id']}/dismiss").get_json()["ok"]
    assert client.get("/api/imports/pending").get_json()["imports"] == []
    agenda = client.get(f"/api/agenda?from={future(0)}&to={future(30)}").get_json()
    assert agenda["events"] == []


def test_email_multipart_extraction_and_hint_default(client, monkeypatch):
    import email.message
    msg = email.message.EmailMessage()
    msg["Subject"] = "Fete"
    msg["From"] = "x@y.z"
    msg.set_content("The school fete is on soon.")
    with open(os.path.join(FIXTURES, "school_flyer.pdf"), "rb") as f:
        msg.add_attachment(f.read(), maintype="application",
                           subtype="pdf", filename="flyer.pdf")
    text = magic_import.extract_text(msg)
    assert "school fete" in text and "Book Week Parade" in text

    # person_hint None -> Family default at approve time
    stub_llm(monkeypatch, json.dumps({"events": [
        {"title": "Fete", "date": future(14), "all_day": True,
         "person_hint": None}]}))
    magic_import.process_message("Fete", "x@y.z", text)
    imp = client.get("/api/imports/pending").get_json()["imports"][0]
    r = client.post(f"/api/imports/{imp['id']}/approve",
                    json={"events": [{"index": 0}]}).get_json()
    assert r["created"][0]["person_id"] == "family"


# ------------------------------------------------------------------ poll_inbox retry cap

def test_poison_message_retried_three_times_then_given_up(monkeypatch):
    db.set_json_setting(magic_import.FAIL_COUNTS_SETTING, {})   # clean slate

    mailbox = FakeMailbox()
    mailbox.add(b"1", raw_email("Bad flyer", "school@example.com", "body",
                                "<poison@example.test>"))
    install_fake_imap(monkeypatch, mailbox)

    def always_throws(subject, from_addr, body):
        raise RuntimeError("boom")

    monkeypatch.setattr(magic_import, "process_message", always_throws)

    for attempt in (1, 2, 3):
        total, gave_up = magic_import.poll_inbox()
        assert total == 0
        if attempt < 3:
            assert gave_up == 0
            assert b"1" not in mailbox.seen           # left UNSEEN, will retry
            assert magic_import._load_fail_counts() == {"<poison@example.test>": attempt}
        else:
            assert gave_up == 1
            assert b"1" in mailbox.seen                # given up: marked Seen
            assert magic_import._load_fail_counts() == {}   # counter dropped

    # a further poll finds nothing left UNSEEN — never retried again
    total, gave_up = magic_import.poll_inbox()
    assert (total, gave_up) == (0, 0)


def test_success_after_one_failure_clears_counter(monkeypatch):
    db.set_json_setting(magic_import.FAIL_COUNTS_SETTING, {})
    mailbox = FakeMailbox()
    mailbox.add(b"1", raw_email("Newsletter", "school@example.com", "body",
                                "<recovers@example.test>"))
    install_fake_imap(monkeypatch, mailbox)

    calls = {"n": 0}

    def flaky(subject, from_addr, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient LLM error")
        return 0

    monkeypatch.setattr(magic_import, "process_message", flaky)

    total, gave_up = magic_import.poll_inbox()
    assert (total, gave_up) == (0, 0)
    assert b"1" not in mailbox.seen
    assert magic_import._load_fail_counts() == {"<recovers@example.test>": 1}

    total, gave_up = magic_import.poll_inbox()
    assert (total, gave_up) == (0, 0)
    assert b"1" in mailbox.seen
    # succeeded on the second attempt: counter cleared, not left at 1
    assert magic_import._load_fail_counts() == {}
