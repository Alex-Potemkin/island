"""Accounts and online services: Google Calendar (OAuth), any calendar by iCal link, GitHub notifications.

Secrets (refresh tokens, GitHub token) are stored with config.protect: DPAPI on Windows, a 0600 file
on Linux. Endpoints are class attributes so tests can point them at a local fake server.
"""
import base64
import datetime as dt
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from PySide6.QtCore import QObject, QTimer, Signal

from . import bridge, config

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
    """Merges Google + iCal events, refreshes every 10 minutes, and raises "starts soon" notifications."""
    changed = Signal()

    def __init__(self, settings, notify):
        super().__init__()
        self.settings = settings
        self.notify = notify
        self.google = GoogleAccount(settings)
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
        QTimer.singleShot(2500, self.refresh)

    def ics_list(self):
        return [c for c in (self.settings.get("accounts.ics") or []) if c.get("url")]

    def has_sources(self):
        return self.google.is_connected() or bool(self.ics_list())

    def refresh(self):
        if self._busy or not self.has_sources():
            if not self.has_sources():
                self.events, self.errors = [], {}
                self.changed.emit()
            return
        self._busy = True
        bridge.run_thread(self._fetch, self._done, self._fail)

    def _fetch(self):
        now = dt.datetime.now(LOCAL_TZ)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)
        end = start + dt.timedelta(days=40)
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
        events.sort(key=lambda e: (e["start"], not e["all_day"]))
        return events, errors

    def _done(self, res):
        self._busy = False
        self.events, self.errors = res
        self.updated = time.time()
        self.changed.emit()
        self._check_soon()

    def _fail(self, exc):
        self._busy = False
        self.errors = {"": str(exc)}
        self.changed.emit()

    def upcoming(self, days=7):
        now = dt.datetime.now(LOCAL_TZ)
        end = now + dt.timedelta(days=days)
        return [e for e in self.events if e["end"] > now and e["start"] < end]

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
        self._timer = QTimer(self, interval=120 * 1000, timeout=self.poll)
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

