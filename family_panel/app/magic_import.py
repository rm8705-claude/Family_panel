"""Magic Import: dedicated-inbox email -> proposed calendar events.

Flow: IMAP poll for UNSEEN -> extract plain text (+ PDF attachment text via
pdfplumber) -> LLM extraction with a strict-JSON prompt -> insert as a
'pending' magic_imports row. Nothing is ever auto-approved; the panel's
review sheet turns pending rows into events_local rows.

Messages are marked \\Seen only AFTER a successful DB insert, so a crash
mid-poll means the message is retried next cycle (idempotent).

A message whose processing keeps throwing (bad attachment, LLM outage for
that one email, ...) would otherwise stay UNSEEN and be retried — and paid
for — on every single poll forever. Failures are counted per message (by
Message-ID, falling back to the IMAP uid) in the "magic:fail_counts" setting;
after MAX_FAIL_ATTEMPTS the message is marked Seen anyway and given up on.
That cap only changes the fate of repeat failers — the crash-safety property
above still holds for the success path.
"""
import email
import email.header
import imaplib
import io
import json
import logging
import re
from datetime import date, datetime, time, timedelta

import db
from config import env

log = logging.getLogger("family-panel.magic_import")

MAX_BODY_CHARS = 12000
MAX_PDF_PAGES = 10
LLM_MAX_TOKENS = 2000
MAX_FAIL_ATTEMPTS = 3
FAIL_COUNTS_SETTING = "magic:fail_counts"


class NotConfigured(Exception):
    pass


EXTRACTION_PROMPT = """You extract calendar events from an email sent to a family's \
shared calendar inbox (a Brisbane, Australia household: two adults, one young child).

Today's date is {today} (Australia/Brisbane).

Read the email below and extract any real, attendable calendar events (school events, \
appointments, sports fixtures, parties, excursions, concerts, closures that matter). \
Ignore marketing, newsletters with no dates, receipts, and generic reminders without a \
specific date.

Rules:
- Dates must be ISO YYYY-MM-DD. Resolve relative or partial dates using today's date; \
if a date has no year, choose the next future occurrence.
- Times are 24-hour HH:MM. If no start time is given, set start_time to null and \
all_day to true.
- person_hint: "kid" for child/school events, "rohan" or "partner" only if the email \
clearly names them, "family" for whole-family events, else null.
- notes: one short sentence of useful detail (what to bring, cost, RSVP), else null.
- If the email contains no events, return {{"events": []}}.

Respond with ONLY this JSON, no code fences, no commentary:
{{"events": [{{"title": str, "date": "YYYY-MM-DD", "start_time": "HH:MM"|null, \
"end_time": "HH:MM"|null, "all_day": bool, "person_hint": str|null, \
"location": str|null, "notes": str|null}}]}}

EMAIL:
Subject: {subject}
From: {from_addr}

{body}
"""


# ------------------------------------------------------------------ LLM call

def call_llm(prompt: str) -> str:
    provider = env("LLM_PROVIDER", "anthropic").lower()
    if provider == "lmstudio":
        import requests
        base = env("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
        r = requests.post(f"{base}/chat/completions", json={
            "model": env("LMSTUDIO_MODEL", "local-model"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": LLM_MAX_TOKENS,
        }, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    import anthropic
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=env("LLM_MODEL", "claude-haiku-4-5"),
        max_tokens=LLM_MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


# ------------------------------------------------------------------ parsing

def parse_llm_json(raw: str) -> list[dict]:
    """Defensive parse: strip code fences, find the JSON object, validate."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in LLM response")
    data = json.loads(text[start:end + 1])
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("'events' missing or not a list")
    return [e for e in (validate_event(e) for e in events) if e]


def validate_event(e) -> dict | None:
    if not isinstance(e, dict) or not (e.get("title") or "").strip():
        return None
    try:
        d = date.fromisoformat(str(e.get("date", "")))
    except ValueError:
        return None
    # reject stale events: anything ended more than 7 days ago
    if d < db.local_today() - timedelta(days=7):
        return None

    def clean_time(v):
        if not v:
            return None
        try:
            return time.fromisoformat(str(v)).strftime("%H:%M")
        except ValueError:
            return None

    start_t = clean_time(e.get("start_time"))
    end_t = clean_time(e.get("end_time"))
    all_day = bool(e.get("all_day")) or start_t is None
    hint = e.get("person_hint")
    if hint is not None:
        hint = str(hint).lower()
    return {
        "title": str(e["title"]).strip()[:200],
        "date": d.isoformat(),
        "start_time": None if all_day else start_t,
        "end_time": None if all_day else end_t,
        "all_day": all_day,
        "person_hint": hint,
        "location": (str(e["location"]).strip()[:200] if e.get("location") else None),
        "notes": (str(e["notes"]).strip()[:500] if e.get("notes") else None),
    }


# ------------------------------------------------------------------ email

def decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def extract_text(msg: email.message.Message) -> str:
    """Plain-text body + text from PDF attachments. Images ignored (v1)."""
    chunks = []
    html_fallback = []
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if ctype == "text/plain" and "attachment" not in disp:
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace"))
        elif ctype == "text/html" and "attachment" not in disp:
            payload = part.get_payload(decode=True)
            if payload:
                html = payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace")
                html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                              flags=re.DOTALL | re.IGNORECASE)
                html_fallback.append(re.sub(r"<[^>]+>", " ", html))
        elif ctype == "application/pdf" or (
                part.get_filename() or "").lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(pdf_text(payload))
    if not any(c.strip() for c in chunks) and html_fallback:
        chunks = html_fallback
    text = "\n\n".join(c for c in chunks if c.strip())
    return re.sub(r"[ \t]+", " ", text)[:MAX_BODY_CHARS]


def pdf_text(data: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages[:MAX_PDF_PAGES]
            return "\n".join((p.extract_text() or "") for p in pages)
    except Exception as e:
        log.warning("PDF extraction failed: %s", e)
        return ""


# ------------------------------------------------------------------ retry cap

def _message_key(msg: email.message.Message, num: bytes) -> str:
    """A stable id for the per-message failure counter.

    Message-ID is the natural key (stable across polls); a handful of real
    senders omit it, so we fall back to the IMAP sequence/uid number, which
    is still stable for as long as the message stays UNSEEN in this mailbox.
    """
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid
    return "uid:" + (num.decode() if isinstance(num, bytes) else str(num))


def _load_fail_counts() -> dict:
    return db.get_json_setting(FAIL_COUNTS_SETTING, {}) or {}


def _save_fail_counts(counts: dict) -> None:
    db.set_json_setting(FAIL_COUNTS_SETTING, counts)


# ------------------------------------------------------------------ pipeline

def process_message(subject: str, from_addr: str, body: str) -> int:
    """Run extraction on one email and store pending rows. Returns event count.

    Separated from IMAP plumbing so tests can drive it with fixtures.
    """
    prompt = EXTRACTION_PROMPT.format(
        today=db.local_today().isoformat(),
        subject=subject or "(no subject)",
        from_addr=from_addr or "(unknown)",
        body=body or "(empty)")
    raw = call_llm(prompt)
    events = parse_llm_json(raw)
    if not events:
        return 0        # no-events email: zero pending rows, no crash
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT INTO magic_imports(received_at, from_addr, subject,"
            " body_excerpt, extracted_json, status) VALUES(?,?,?,?,?, 'pending')",
            (db.utc_now_iso(), from_addr, subject, (body or "")[:500],
             json.dumps({"events": events})))
    return len(events)


def poll_inbox() -> tuple[int, int]:
    """-> (proposed_event_count, gave_up_count)."""
    host = env("IMAP_HOST")
    user = env("IMAP_USER")
    password = env("IMAP_APP_PASSWORD")
    if not (host and user and password):
        raise NotConfigured("IMAP_HOST/IMAP_USER/IMAP_APP_PASSWORD not set")

    total = 0
    gave_up = 0
    fail_counts = _load_fail_counts()
    # timeout=30: a dead NAT session must fail the poll, not hang the worker
    # forever (max_instances=1 means a stuck poll wedges every future one too).
    with imaplib.IMAP4_SSL(host, timeout=30) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        for num in data[0].split():
            # fetch without setting \Seen; only mark after a successful insert
            status, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_header(msg.get("Subject", ""))
            from_addr = decode_header(msg.get("From", ""))
            key = _message_key(msg, num)
            try:
                n = process_message(subject, from_addr, extract_text(msg))
                total += n
                imap.store(num, "+FLAGS", "\\Seen")
                fail_counts.pop(key, None)   # clear any earlier failure streak
                log.info("magic import: %r -> %d proposed events", subject, n)
            except Exception as e:
                attempts = fail_counts.get(key, 0) + 1
                if attempts >= MAX_FAIL_ATTEMPTS:
                    # poison message: stop paying to retry it forever
                    imap.store(num, "+FLAGS", "\\Seen")
                    fail_counts.pop(key, None)
                    gave_up += 1
                    log.warning(
                        "magic import gave up on %r after %d attempts: %s",
                        subject, attempts, e)
                else:
                    # leave UNSEEN so it retries next poll
                    fail_counts[key] = attempts
                    log.warning(
                        "magic import failed for %r (attempt %d/%d): %s",
                        subject, attempts, MAX_FAIL_ATTEMPTS, e)
    _save_fail_counts(fail_counts)
    return total, gave_up
