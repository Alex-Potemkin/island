"""Accounts and notifications against a local fake server (no real Google/GitHub needed).

    python tests/check_integrations.py

Covers: Google OAuth (loopback + PKCE, code exchange, refresh, primary e-mail), Google events, iCal
parsing (time zones, RRULE, EXDATE, all-day, cancelled, folded lines), "starts soon" notifications,
GitHub notifications (+304), notification styles (card / pill / off), quiet hours and card timeout.
"""
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ISLAND_HOME"] = tempfile.mkdtemp(prefix="island-int-")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from island_app import integrations, osapi  # noqa: E402

results = []
LOCAL = dt.datetime.now().astimezone().tzinfo
SOON = (dt.datetime.now(LOCAL) + dt.timedelta(minutes=8)).replace(microsecond=0)
STATE = {"challenge": None, "gh_calls": 0}


def check(name, ok, info=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0",
    "BEGIN:VEVENT", "UID:weekly-1", "SUMMARY:Планёрка\\, команда", "LOCATION:Переговорная 2",
    "DTSTART;TZID=Europe/Moscow:20261005T100000", "DTEND;TZID=Europe/Moscow:20261005T103000",
    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE", "EXDATE;TZID=Europe/Moscow:20261028T100000", "END:VEVENT",
    "BEGIN:VEVENT", "UID:allday-1", "SUMMARY:День рождения", "DTSTART;VALUE=DATE:20261027",
    "DTEND;VALUE=DATE:20261028", "END:VEVENT",
    "BEGIN:VEVENT", "UID:gone-1", "SUMMARY:Отменено", "STATUS:CANCELLED", "DTSTART:20261027T090000Z",
    "END:VEVENT",
    "BEGIN:VEVENT", "UID:long-1", "SUMMARY:Очень длинное назв", " ание события", "DTSTART:20261029T070000Z",
    "DTEND:20261029T080000Z", "END:VEVENT",
    "END:VCALENDAR", ""])


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code, payload=None, ctype="application/json", headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, token):
        return self.headers.get("Authorization") == f"Bearer {token}"

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/auth":
            STATE["challenge"] = q.get("code_challenge")
            ok = q.get("code_challenge_method") == "S256" and q.get("access_type") == "offline"
            loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "CODE123" if ok else "", "state": q["state"]})
            self.send_response(302)
            self.send_header("Location", loc)
            self.end_headers()
        elif u.path == "/cal/calendars/primary":
            self._send(200 if self._authed("AT1") or self._authed("AT2") else 401, {"id": "me@example.com"})
        elif u.path == "/cal/users/me/calendarList":
            self._send(200, {"items": [{"id": "me@example.com", "selected": True, "summary": "Мой", "primary": True}]})
        elif u.path.startswith("/cal/calendars/") and u.path.endswith("/events"):
            if not (self._authed("AT1") or self._authed("AT2")):
                return self._send(401, {"error": {"message": "unauthorized"}})
            today = dt.date.today().isoformat()
            self._send(200, {"items": [
                {"id": "ev1", "summary": "Созвон по дизайну", "htmlLink": "https://calendar.google.com/ev1",
                 "start": {"dateTime": SOON.isoformat()}, "end": {"dateTime": (SOON + dt.timedelta(hours=1)).isoformat()},
                 "location": "Zoom"},
                {"id": "ev2", "summary": "Отпуск", "start": {"date": today}, "end": {"date": today}},
                {"id": "ev3", "summary": "Отменили", "status": "cancelled", "start": {"date": today}, "end": {"date": today}},
            ]})
        elif u.path == "/feed.ics":
            self._send(200, ICS.encode(), "text/calendar")
        elif u.path == "/gh/user":
            self._send(200 if self._authed("ghp_test") else 401, {"login": "octocat"})
        elif u.path == "/gh/notifications":
            STATE["gh_calls"] += 1
            if self.headers.get("If-Modified-Since"):
                return self._send(304, b"")
            now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)
            self._send(200, [{"id": "1", "unread": True, "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "repository": {"full_name": "octo/island"},
                              "subject": {"title": "Fix tray icon", "type": "PullRequest",
                                          "url": "https://api.github.com/repos/octo/island/pulls/5"}}],
                       headers={"Last-Modified": "Mon, 26 Oct 2026 10:00:00 GMT"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        if self.path == "/token":
            if form.get("grant_type") == "authorization_code":
                verifier_ok = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode())
                                                       .digest()).decode().rstrip("=") == STATE["challenge"]
                if form.get("code") == "CODE123" and verifier_ok and form.get("client_secret") == "s3cret":
                    return self._send(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600})
                return self._send(400, {"error": "invalid_grant", "error_description": "bad code or verifier"})
            if form.get("grant_type") == "refresh_token" and form.get("refresh_token") == "RT1":
                return self._send(200, {"access_token": "AT2", "expires_in": 3600})
            return self._send(400, {"error": "invalid_grant"})
        self._send(404, {})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    integrations.GoogleAccount.AUTH_URL = base + "/auth"
    integrations.GoogleAccount.TOKEN_URL = base + "/token"
    integrations.GoogleAccount.API = base + "/cal"
    integrations.GitHubService.API = base + "/gh"

    # iCal parsing on its own, with a fixed range
    rng0 = dt.datetime(2026, 10, 26, tzinfo=LOCAL)
    evs = integrations.parse_ics(ICS, rng0, rng0 + dt.timedelta(days=7), "Работа")
    weekly = [e for e in evs if e["title"] == "Планёрка, команда"]
    moscow = dt.datetime(2026, 10, 26, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Europe/Moscow"))
    check("iCal: weekly RRULE minus EXDATE", [e["start"].date().isoformat() for e in weekly] == ["2026-10-26"],
          [e["start"].isoformat() for e in weekly])
    check("iCal: TZID converted to local time", weekly and weekly[0]["start"] == moscow.astimezone(LOCAL))
    check("iCal: escapes, location, all-day, folded lines, cancelled skipped",
          weekly and weekly[0]["location"] == "Переговорная 2"
          and any(e["all_day"] and e["title"] == "День рождения" for e in evs)
          and any(e["title"] == "Очень длинное название события" for e in evs)
          and not any(e["title"] == "Отменено" for e in evs), [e["title"] for e in evs])
    check("github: api url -> html url",
          integrations.html_url("https://api.github.com/repos/o/r/pulls/5") == "https://github.com/o/r/pull/5")

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from island_app.app import App
    app = App(qapp, ["--demo"])
    app.hard_exit = False
    app.sys.download_finished.disconnect()     # real downloads on this PC must not add cards
    app.sys.downloads_changed.disconnect()
    app.system_notifications.stop()
    app.system_notifications._timer.stop()
    opened = []
    import webbrowser
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    s = app.settings
    state = {}
    steps = []

    def step(delay, fn):
        steps.append((delay, fn))

    def google_connect():
        s.set("accounts.google.client_id", "test-client")
        s.set("accounts.google.client_secret", "s3cret")
        g = app.calendar.google
        g.connected.connect(lambda email: state.__setitem__("email", email))
        g.failed.connect(lambda msg: state.__setitem__("fail", msg))
        # the "browser": follows the redirect to Island's loopback listener, off the sign-in thread
        g.connect(open_url=lambda url: threading.Thread(target=lambda: urllib.request.urlopen(url).read(),
                                                        daemon=True).start())

    def after_connect():
        stored = s.get("accounts.google.refresh") or ""
        check("Google: OAuth sign-in with PKCE", state.get("email") == "me@example.com",
              state.get("fail") or state.get("email"))
        check("Google: refresh token stored encrypted" if osapi.IS_WINDOWS else "Google: refresh token stored",
              stored.startswith("dpapi:") if osapi.IS_WINDOWS else stored == "RT1", stored[:12])
        app.calendar.google._access = None          # force the refresh-token path
        s.set("accounts.ics", [{"name": "Работа", "url": base + "/feed.ics"}])
        s.set("notifications", {"style": "card", "duration": 6, "sound": "none", "event_lead": 10, "sources": {}})
        app.calendar.refresh()

    def after_refresh():
        cal = app.calendar
        titles = [e["title"] for e in cal.events]
        check("Google + iCal events merged", "Созвон по дизайну" in titles and "Планёрка, команда" in titles,
              cal.errors or titles[:6])
        check("Google: refresh token used for a new access token", cal.google._access == "AT2")
        check("Google: cancelled events skipped, all-day kept", "Отменили" not in titles and "Отпуск" in titles)
        hist = app.notifications.history
        check("event starting in 8 min raised a notification",
              any(n["source"] == "calendar" and n["title"] == "Созвон по дизайну" for n in hist))
        check("card style shows the notification in the pill", app.island.mode == "toast"
              and app.island.toast and app.island.toast["title"] == "Созвон по дизайну", app.island.mode)

    def after_timeout():
        check("card disappears after its duration", app.island.mode != "toast", app.island.mode)
        s.set("notifications", {"style": "card", "duration": 8, "sound": "none", "event_lead": 10,
                                "sources": {"github": "pill"}})
        app.github.set_token("ghp_test")

    def after_github():
        gh = app.github
        n = next((n for n in app.notifications.history if n["source"] == "github"), None)
        check("GitHub: login and unread count", gh.login == "octocat" and len(gh.unread) == 1, gh.error)
        check("GitHub: new item raised a notification with the web link",
              n and n["url"] == "https://github.com/octo/island/pull/5", n)
        check("pill style shows a one-line message, not a card",
              app.activities.get("alert", {}).get("title") == "Fix tray icon" and app.island.mode != "toast")
        app.github.poll()

    def after_github2():
        check("GitHub: second poll uses If-Modified-Since (304)", app.github.error == "" and STATE["gh_calls"] >= 2)
        app.clear_alert()
        s.set("notifications", {"style": "off", "sound": "none", "sources": {}})
        before = len(app.notifications.history)
        app.notifications.push("scripts", "Сборка", "готово")
        check("off: kept in history only", len(app.notifications.history) == before + 1
              and app.island.mode != "toast" and "alert" not in app.activities)
        h = dt.datetime.now().hour
        s.set("notifications", {"style": "card", "duration": 3, "sound": "none", "sources": {},
                                "quiet": {"enabled": True, "from": h, "to": (h + 1) % 24}})
        app.notifications.push("calendar", "Тихо", "в тихие часы")
        check("quiet hours: no card", app.island.mode != "toast")
        app.notifications.push("reminders", "Важно", "напоминание")
        check("quiet hours: reminders still shown", app.island.mode == "toast" and app.island.toast["title"] == "Важно")

    def click():
        n = dict(app.island.toast)
        app.island.dismiss_toast()
        app.notifications.open({"url": "https://example.com/x"})
        check("click opens the link and closes the card", opened[-1:] == ["https://example.com/x"]
              and app.island.mode != "toast", n["title"])

    step(300, google_connect)
    step(2500, after_connect)
    step(3000, after_refresh)
    step(6500, after_timeout)
    step(2500, after_github)
    step(1500, after_github2)
    step(300, click)

    def run(i=0):
        if i >= len(steps):
            print(f"\n{sum(results)}/{len(results)} passed", flush=True)
            server.shutdown()
            qapp.exit(0 if all(results) else 1)
            return
        d, fn = steps[i]

        def go():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"step {fn.__name__} ran", False, exc)
            run(i + 1)
        QTimer.singleShot(d, go)
    run()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
