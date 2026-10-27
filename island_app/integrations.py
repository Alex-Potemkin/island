"""Accounts and online services: Google Calendar (OAuth), any calendar by iCal link, GitHub notifications.

Secrets (refresh tokens, GitHub token) are stored with config.protect: DPAPI on Windows, a 0600 file
on Linux. Endpoints are class attributes so tests can point them at a local fake server.
"""
import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from PySide6.QtCore import QObject, QTimer, Signal

from . import bridge, config, osapi, perf

LOCAL_TZ = dt.datetime.now().astimezone().tzinfo


def _http(url, data=None, headers=None, timeout=15):
    """(status, headers, body-bytes); form-encodes dict data."""
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "Island/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def _json(url, data=None, headers=None):
    status, _h, body = _http(url, data, headers)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    if status >= 400:
        msg = payload.get("error_description") or payload.get("message") or payload.get("error") or f"HTTP {status}"
        if isinstance(msg, dict):
            msg = msg.get("message", str(msg))
        raise RuntimeError(str(msg))
    return payload


def event(title, start, end, all_day=False, location="", source="", url="", uid=""):
    return {"title": title or "Без названия", "start": start, "end": end, "all_day": all_day,
            "location": location, "source": source, "url": url, "id": uid or f"{source}:{title}:{start}"}


# ================================================================ Google Calendar (OAuth 2.0, installed app)


class GoogleAccount(QObject):
    """OAuth for desktop apps: the browser signs in, Google redirects to a loopback port with a code,
    PKCE protects the exchange. Needs a "Desktop app" OAuth client from Google Cloud Console."""
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    API = "https://www.googleapis.com/calendar/v3"
    SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

    connected = Signal(str)     # account e-mail
    failed = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._access = None
        self._expires = 0.0
        self.busy = False

    # -------- state
    @property
    def client_id(self):
        return (self.settings.get("accounts.google.client_id") or "").strip()

    @property
    def client_secret(self):
        return config.unprotect(self.settings.get("accounts.google.client_secret") or "")

    @property
    def email(self):
        return self.settings.get("accounts.google.email") or ""

    def is_connected(self):
        return bool(self.settings.get("accounts.google.refresh"))

    def disconnect(self):
        self.settings.set("accounts.google.refresh", "", save=False)
        self.settings.set("accounts.google.email", "")
        self._access = None

    # -------- sign-in
    def connect(self, open_url=webbrowser.open, timeout=180):
        if not self.client_id:
            self.failed.emit("Укажите Client ID (см. «Как получить»).")
            return
        if self.busy:
            return
        self.busy = True
        bridge.run_thread(self._sign_in, self._signed_in, self._sign_in_failed, open_url, timeout)

    def _sign_in(self, open_url, timeout):
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] != state:
                    self.send_response(400)
                    self.end_headers()
                    return
                result["code"] = q.get("code", [""])[0]
                result["error"] = q.get("error", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                ok = "Готово, можно закрыть вкладку и вернуться к Island." if result["code"] else "Вход отменён."
                self.wfile.write(f"<html><body style='font:16px sans-serif;margin:3em'>{ok}</body></html>".encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_address[1]}"
        params = {"client_id": self.client_id, "redirect_uri": redirect, "response_type": "code",
                  "scope": self.SCOPE, "code_challenge": challenge, "code_challenge_method": "S256",
                  "state": state, "access_type": "offline", "prompt": "consent"}
        open_url(self.AUTH_URL + "?" + urllib.parse.urlencode(params))
        end = time.time() + timeout
        try:
            while "code" not in result and time.time() < end:
                server.handle_request()
        finally:
            server.server_close()
        if not result.get("code"):
            raise RuntimeError(result.get("error") or "Вход не завершён")
        data = {"client_id": self.client_id, "code": result["code"], "code_verifier": verifier,
                "grant_type": "authorization_code", "redirect_uri": redirect}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        tok = _json(self.TOKEN_URL, data)
        if not tok.get("refresh_token"):
            raise RuntimeError("Google не выдал refresh token")
        self._access, self._expires = tok.get("access_token"), time.time() + int(tok.get("expires_in", 3600)) - 60
        primary = _json(f"{self.API}/calendars/primary", headers=self._auth())
        return tok["refresh_token"], primary.get("id", "")

    def _signed_in(self, res):
        self.busy = False
        refresh, email = res
        self.settings.set("accounts.google.refresh", config.protect(refresh) or refresh, save=False)
        self.settings.set("accounts.google.email", email)
        self.connected.emit(email)

    def _sign_in_failed(self, exc):
        self.busy = False
        self.failed.emit(str(exc))

    # -------- API (blocking; call from worker threads)
    def _auth(self):
        return {"Authorization": f"Bearer {self._access}"}

    def _token(self):
        if self._access and time.time() < self._expires:
            return
        data = {"client_id": self.client_id, "grant_type": "refresh_token",
                "refresh_token": config.unprotect(self.settings.get("accounts.google.refresh") or "")}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        tok = _json(self.TOKEN_URL, data)
        self._access, self._expires = tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60

    def events(self, start, end):
        self._token()
        cals = _json(f"{self.API}/users/me/calendarList?minAccessRole=reader", headers=self._auth()).get("items", [])
        cals = [c for c in cals if c.get("selected", c.get("primary"))] or [{"id": "primary", "summary": ""}]
        out = []
        for cal in cals:
            q = urllib.parse.urlencode({"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                        "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})
            cid = urllib.parse.quote(cal["id"], safe="")
            for e in _json(f"{self.API}/calendars/{cid}/events?{q}", headers=self._auth()).get("items", []):
                if e.get("status") == "cancelled":
                    continue
                s, f = e.get("start", {}), e.get("end", {})
                if "date" in s:
                    st = dt.datetime.fromisoformat(s["date"]).replace(tzinfo=LOCAL_TZ)
                    en = dt.datetime.fromisoformat(f.get("date", s["date"])).replace(tzinfo=LOCAL_TZ)
                    all_day = True
                else:
                    st = _iso(s.get("dateTime"))
                    en = _iso(f.get("dateTime")) or st
                    all_day = False
                if st:
                    out.append(event(e.get("summary"), st, en, all_day, e.get("location", ""),
                                     "Google" + (f" · {cal.get('summary')}" if len(cals) > 1 else ""),
                                     e.get("htmlLink", ""), "g:" + e.get("id", "")))
        return out


def _iso(v):
    if not v:
        return None
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(LOCAL_TZ)


# ================================================================ iCal (.ics) links


def _unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_time(value, params):
    """DTSTART/DTEND value -> (aware datetime, all_day)."""
    from zoneinfo import ZoneInfo
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        d = dt.datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=LOCAL_TZ), True
    v = value.rstrip("Z")
    d = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return d.replace(tzinfo=dt.timezone.utc).astimezone(LOCAL_TZ), False
    tzid = params.get("TZID")
    if tzid:
        try:
            return d.replace(tzinfo=ZoneInfo(tzid.strip('"'))).astimezone(LOCAL_TZ), False
        except Exception:
            pass
    return d.replace(tzinfo=LOCAL_TZ), False


def _unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(text, start, end, source="iCal"):
    """Events overlapping [start, end): simple events plus RRULE/EXDATE recurrences (python-dateutil)."""
    from dateutil.rrule import rrulestr
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdate": []}
            continue
        if line == "END:VEVENT":
            if cur is not None and "DTSTART" in cur:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *pairs = head.split(";")
        params = dict(p.split("=", 1) for p in pairs if "=" in p)
        name = name.upper()
        if name in ("DTSTART", "DTEND"):
            cur[name] = _ics_time(value, params)
        elif name == "EXDATE":
            for v in value.split(","):
                cur["exdate"].append(_ics_time(v, params)[0])
        elif name in ("SUMMARY", "LOCATION", "UID", "RRULE", "URL", "STATUS"):
            cur[name] = _unescape(value) if name in ("SUMMARY", "LOCATION") else value
    out = []
    for e in events:
        if e.get("STATUS", "").upper() == "CANCELLED":
            continue
        st, all_day = e["DTSTART"]
        en = e.get("DTEND", (st + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1)), all_day))[0]
        dur = en - st
        starts = [st]
        if e.get("RRULE"):
            rule = rrulestr(e["RRULE"], dtstart=st, forceset=True, ignoretz=False)
            for x in e["exdate"]:
                rule.exdate(x)
            starts = rule.between(start - dur, end, inc=True)
        for s in starts:
            if s + dur > start and s < end:
                out.append(event(e.get("SUMMARY"), s, s + dur, all_day, e.get("LOCATION", ""), source,
                                 e.get("URL", ""), f"ics:{e.get('UID', '')}:{s.isoformat()}"))
    return out


# ================================================================ calendar service


class CalendarService(QObject):
    """Merges the local calendar ("Мой", editable) with Google + iCal (read-only), refreshes remote calendars
    every 10 minutes and raises "starts soon" notifications."""
    changed = Signal()
    PAST_DAYS, FUTURE_DAYS = 62, 124        # window kept in memory: enough to page through months
    REPEATS = {"none": "Не повторять", "daily": "Каждый день", "weekly": "Каждую неделю",
               "monthly": "Каждый месяц", "yearly": "Каждый год"}

    def __init__(self, settings, notify, store=None):
        super().__init__()
        self.settings = settings
        self.notify = notify
        self.store = store
        self.google = GoogleAccount(settings)
        self.remote = []
        self.events = []
        self.errors = {}
        self.updated = 0.0
        self._notified = set()
        self._busy = False
        self.google.connected.connect(lambda _e: self.refresh())
        self._refresh_timer = QTimer(self, interval=10 * 60 * 1000, timeout=self.refresh)
        self._refresh_timer.start()
        self._soon = QTimer(self, interval=30 * 1000, timeout=self._check_soon)
        self._soon.start()
        self._merge()
        QTimer.singleShot(2500, self.refresh)

    # -------- sources
    def ics_list(self):
        return [c for c in (self.settings.get("accounts.ics") or []) if c.get("url")]

    def has_accounts(self):
        return self.google.is_connected() or bool(self.ics_list())

    def has_sources(self):
        return True     # the local calendar is always there

    def window(self):
        today = dt.datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        return today - dt.timedelta(days=self.PAST_DAYS), today + dt.timedelta(days=self.FUTURE_DAYS)

    def refresh(self):
        if self._busy:
            return
        if not self.has_accounts():
            self.remote, self.errors = [], {}
            self._merge()
            return
        self._busy = True
        bridge.run_thread(self._fetch, self._done, self._fail)

    def _fetch(self):
        start, end = self.window()
        events, errors = [], {}
        if self.google.is_connected():
            try:
                events += self.google.events(start, end)
            except Exception as exc:  # noqa: BLE001 - shown in settings
                errors["Google"] = str(exc)
        for cal in self.ics_list():
            url = cal["url"].replace("webcal://", "https://", 1)
            try:
                status, _h, body = _http(url, timeout=20)
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")
                events += parse_ics(body.decode("utf-8", "replace"), start, end, cal.get("name") or "iCal")
            except Exception as exc:  # noqa: BLE001
                errors[cal.get("name") or url] = str(exc)
        return events, errors

    def _done(self, res):
        self._busy = False
        self.remote, self.errors = res
        self.updated = time.time()
        self._merge()
        self._check_soon()

    def _fail(self, exc):
        self._busy = False
        self.errors = {"": str(exc)}
        self.changed.emit()

    # -------- the local calendar
    @property
    def local(self):
        return self.store.data.setdefault("events", []) if self.store is not None else []

    def _expand_local(self, start, end):
        from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule
        freq = {"daily": DAILY, "weekly": WEEKLY, "monthly": MONTHLY, "yearly": YEARLY}
        out = []
        for e in self.local:
            st = dt.datetime.fromisoformat(e["start"]).astimezone(LOCAL_TZ)
            en = dt.datetime.fromisoformat(e["end"]).astimezone(LOCAL_TZ)
            dur = max(en - st, dt.timedelta(minutes=1))
            if e.get("repeat", "none") in freq:
                starts = rrule(freq[e["repeat"]], dtstart=st).between(start - dur, end, inc=True)
            else:
                starts = [st]
            for s in starts:
                if s + dur > start and s < end:
                    ev = event(e["title"], s, s + dur, e.get("all_day", False), e.get("location", ""), "Мой", "",
                               f"local:{e['id']}:{s.isoformat()}")
                    ev.update(local=True, lid=e["id"], repeat=e.get("repeat", "none"))
                    out.append(ev)
        return out

    def _merge(self):
        start, end = self.window()
        self.events = sorted(self.remote + self._expand_local(start, end),
                             key=lambda e: (e["start"], not e["all_day"]))
        self.changed.emit()

    def save_event(self, data, lid=None):
        """Create (lid=None) or update a local event. data: title, start, end (aware datetimes), all_day,
        location, repeat."""
        rec = {"title": data.get("title") or "Без названия", "start": data["start"].isoformat(),
               "end": data["end"].isoformat(), "all_day": bool(data.get("all_day")),
               "location": data.get("location", ""), "repeat": data.get("repeat", "none")}
        if lid:
            for e in self.local:
                if e["id"] == lid:
                    e.update(rec)
                    break
        else:
            import uuid
            rec["id"] = uuid.uuid4().hex[:10]
            self.local.append(rec)
            lid = rec["id"]
        self.store.touch()
        self._merge()
        return lid

    def delete_event(self, lid):
        self.store.data["events"] = [e for e in self.local if e["id"] != lid]
        self.store.touch()
        self._merge()

    def local_record(self, lid):
        return next((e for e in self.local if e["id"] == lid), None)

    # -------- queries
    def upcoming(self, days=7):
        now = dt.datetime.now(LOCAL_TZ)
        end = now + dt.timedelta(days=days)
        return [e for e in self.events if e["end"] > now and e["start"] < end]

    def between(self, start, end):
        return [e for e in self.events if e["end"] > start and e["start"] < end]

    def on_day(self, day):
        return [e for e in self.events if e["start"].date() <= day <= (e["end"] - dt.timedelta(seconds=1)).date()]

    def _check_soon(self):
        lead = int(self.settings.get("notifications.event_lead", 10))
        now = dt.datetime.now(LOCAL_TZ)
        for e in self.events:
            if e["all_day"]:
                continue
            key = (e["id"], e["start"].isoformat())
            delta = (e["start"] - now).total_seconds()
            if key not in self._notified and -60 <= delta <= lead * 60:
                self._notified.add(key)
                mins = max(0, round(delta / 60))
                when = "сейчас" if mins == 0 else f"через {mins} мин"
                body = f"{when} · {e['start']:%H:%M}" + (f" · {e['location']}" if e["location"] else "")
                self.notify("calendar", e["title"], body, url=e["url"])


# ================================================================ GitHub notifications


class GitHubService(QObject):
    """Polls /notifications with a personal access token (classic, scope "notifications")."""
    API = "https://api.github.com"
    changed = Signal()

    def __init__(self, settings, notify):
        super().__init__()
        self.settings = settings
        self.notify = notify
        self.unread = []
        self.error = ""
        self.login = ""
        self._since = dt.datetime.now(dt.timezone.utc)
        self._seen = set()
        self._modified = None
        self._busy = False
        self._timer = perf.timer(QTimer(self, timeout=self.poll), 120 * 1000, 300 * 1000)
        self._timer.start()
        QTimer.singleShot(3000, self.poll)

    @property
    def token(self):
        return config.unprotect(self.settings.get("accounts.github.token") or "")

    def set_token(self, token):
        self.settings.set("accounts.github.token", (config.protect(token) or token) if token else "")
        self.unread, self.error, self.login, self._modified = [], "", "", None
        self.changed.emit()
        if token:
            self.poll()

    def poll(self):
        if not self.token or self._busy:
            return
        self._busy = True
        bridge.run_thread(self._fetch, self._done, self._fail)

    def _headers(self):
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        if self._modified:
            h["If-Modified-Since"] = self._modified
        return h

    def _fetch(self):
        login = self.login or _json(f"{self.API}/user", headers={k: v for k, v in self._headers().items()
                                                                  if k != "If-Modified-Since"}).get("login", "")
        status, headers, body = _http(f"{self.API}/notifications", headers=self._headers())
        if status == 304:
            return login, None, headers
        if status >= 400:
            raise RuntimeError(f"GitHub: HTTP {status}")
        return login, json.loads(body.decode("utf-8") or "[]"), headers

    def _done(self, res):
        self._busy = False
        login, items, headers = res
        self.login, self.error = login, ""
        self._modified = headers.get("Last-Modified") or self._modified
        if items is not None:
            self.unread = [i for i in items if i.get("unread")]
            for i in self.unread:
                updated = dt.datetime.fromisoformat(i.get("updated_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
                key = (i.get("id"), i.get("updated_at"))
                if key in self._seen:
                    continue
                self._seen.add(key)
                if updated > self._since:
                    repo = i.get("repository", {}).get("full_name", "")
                    subj = i.get("subject", {})
                    self.notify("github", subj.get("title", "Уведомление"), f"{repo} · {_kind(subj.get('type'))}",
                                url=html_url(subj.get("url") or i.get("repository", {}).get("html_url", "")))
        self.changed.emit()

    def _fail(self, exc):
        self._busy = False
        self.error = str(exc)
        self.changed.emit()


def _kind(t):
    return {"PullRequest": "pull request", "Issue": "issue", "Release": "релиз", "Discussion": "обсуждение",
            "CheckSuite": "CI"}.get(t or "", t or "")


def html_url(api_url):
    """https://api.github.com/repos/o/r/pulls/5 -> https://github.com/o/r/pull/5"""
    if not api_url.startswith("https://api.github.com/repos/"):
        return api_url
    u = api_url.replace("https://api.github.com/repos/", "https://github.com/", 1)
    return u.replace("/pulls/", "/pull/").replace("/commits/", "/commit/")


# ================================================================ notifications from other apps


WPN_DB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db") if os.name == "nt" else ""
_OWN = ("island", "python", "pythonw")


def app_name(primary_id, display=""):
    """Readable app name from a Windows notification handler id (AUMID)."""
    if display:
        return display
    pid = primary_id.split("!")[-1] if "!" in primary_id and primary_id.split("!")[-1] not in ("App", "app") \
        else primary_id.split("!")[0]
    pid = pid.split("_")[0]
    if "\\" in pid or "/" in pid:
        pid = re.split(r"[\\/]", pid)[-1]
    pid = re.sub(r"\.exe$", "", pid, flags=re.I)
    parts = [p for p in pid.split(".") if p and p.lower() not in ("microsoft", "windows", "com", "app")]
    return (parts[-1] if parts else pid) or "Приложение"


def toast_texts(payload):
    """Text lines of a toast XML payload (title first)."""
    s = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload or "")
    texts = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<text[^>]*>(.*?)</text>", s, re.S)]
    return [t for t in texts if t]


def read_windows_toasts(db_path, after_id):
    """New toasts since Notification.Id `after_id`: (max_id, [(id, app, title, body)]). Read-only."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        if after_id is None:  # first look: start from now, don't replay the history
            row = con.execute("SELECT MAX(Id) FROM Notification").fetchone()
            return (row[0] or 0), []
        rows = con.execute(
            "SELECT n.Id, h.PrimaryId, n.Payload, "
            " (SELECT AssetValue FROM HandlerAssets a WHERE a.HandlerId = h.RecordId AND a.AssetKey = 'DisplayName') "
            "FROM Notification n JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
            "WHERE n.Type = 'toast' AND n.Id > ? ORDER BY n.Id", (after_id,)).fetchall()
        top = con.execute("SELECT MAX(Id) FROM Notification").fetchone()[0] or after_id
    finally:
        con.close()
    out = []
    for nid, primary, payload, display in rows:
        if any(o in (primary or "").lower() for o in _OWN):
            continue
        texts = toast_texts(payload)
        if texts:
            out.append((nid, app_name(primary or "", display or ""), texts[0], " · ".join(texts[1:3])))
    return max(top, after_id), out


def windows_notifying_apps(db_path):
    """App ids (AUMIDs) Windows keeps notification settings for, without Island itself."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        rows = con.execute("SELECT PrimaryId FROM NotificationHandler").fetchall()
    finally:
        con.close()
    return sorted({r[0] for r in rows if r[0] and not any(o in r[0].lower() for o in _OWN)})


def parse_dbus_notify(lines):
    """Notify() calls from `dbus-monitor` output: [(app, summary, body)]."""
    out, cur = [], None
    for line in lines:
        line = line.strip()
        if "member=Notify" in line and line.startswith("method call"):
            cur = []
            continue
        if cur is not None:
            m = re.match(r'string "(.*)"$', line)
            if m:
                cur.append(m.group(1).replace('\\"', '"'))
                if len(cur) == 4:  # app_name, app_icon, summary, body
                    app, _icon, summary, body = cur
                    if not any(o in app.lower() for o in _OWN):
                        out.append((app or "Приложение", summary, body))
                    cur = None
            elif line.startswith(("method call", "signal", "method return", "error")):
                cur = None
    return out


class SystemNotifications(QObject):
    """Mirrors other apps' notifications into the island (setting notifications.apps)."""

    def __init__(self, settings, notify):
        super().__init__()
        self.settings = settings
        self.notify = notify
        self.seen_apps = set()
        self._last_id = None
        self._busy = False
        self._proc = None
        self.available = bool(WPN_DB and os.path.exists(WPN_DB)) or bool(shutil.which("dbus-monitor"))
        self._timer = perf.timer(QTimer(self, timeout=self.poll), 1500, 4000)
        self._timer.start()
        self._new_apps = perf.timer(QTimer(self, timeout=self._silence_new), 60 * 1000, 120 * 1000)
        self._new_apps.start()
        self.poll()
        QTimer.singleShot(4000, self._silence_new)

    @property
    def enabled(self):
        return bool(self.settings.get("notifications.apps", True))

    def muted(self):
        return set(self.settings.get("notifications.muted_apps") or [])

    # ---- Island as the main notification centre: Windows banners off, everything shows in the pill
    @property
    def primary(self):
        return bool(self.settings.get("notifications.primary")) and osapi.CAN_SILENCE_BANNERS

    def _backup(self):
        b = self.settings.get("notifications.banners_backup")
        return dict(b) if isinstance(b, dict) else {}

    def set_primary(self, on):
        """on: hide Windows' own banners for every app (remembering what was set); off: put them back."""
        self.settings.set("notifications.primary", bool(on))
        if on:
            self.settings.set("notifications.apps", True)
            self.poll()
            self._silence_new()
        else:
            for app_id, prev in self._backup().items():
                try:
                    osapi.set_banner(app_id, prev)
                except OSError:
                    pass
            self.settings.set("notifications.banners_backup", {})

    def _silence_new(self):
        """Hide banners of apps not handled yet (also apps that start notifying later)."""
        if not self.primary or not WPN_DB or not os.path.exists(WPN_DB):
            return
        backup = self._backup()
        try:
            ids = windows_notifying_apps(WPN_DB)
        except sqlite3.Error:
            return
        new = [i for i in ids if i not in backup]
        for app_id in new:
            try:
                backup[app_id] = osapi.get_banner(app_id)
                osapi.set_banner(app_id, 0)
            except OSError:
                backup.pop(app_id, None)
        if new:
            self.settings.set("notifications.banners_backup", backup)

    def _deliver(self, items):
        self._busy = False
        muted = self.muted()
        for app, title, body in items:
            self.seen_apps.add(app)
            if app not in muted and self.enabled:
                self.notify("apps", title, body, app=app)

    def poll(self):
        if not self.enabled:
            self._last_id = None
            self.stop()
            return
        if os.name == "nt":
            if self._busy or not os.path.exists(WPN_DB):
                return
            self._busy = True

            def work():
                self._last_id, rows = read_windows_toasts(WPN_DB, self._last_id)
                return [(app, title, body) for _i, app, title, body in rows]
            bridge.run_thread(work, self._deliver, lambda _e: setattr(self, "_busy", False))
        elif self._proc is None and shutil.which("dbus-monitor") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            self._proc = subprocess.Popen(
                ["dbus-monitor", "--session", "interface='org.freedesktop.Notifications',member='Notify'"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=self._read_dbus, args=(self._proc,), daemon=True).start()

    def _read_dbus(self, proc):
        buf = []
        for line in proc.stdout:
            buf.append(line)
            if len(buf) > 40 or (line.strip().startswith("string") and len(buf) > 5):
                items = parse_dbus_notify(buf)
                if items:
                    bridge.on_main(self._deliver, items)
                    buf = []
                elif len(buf) > 40:
                    buf = buf[-10:]

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None
