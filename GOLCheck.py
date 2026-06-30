import sublime
import sublime_plugin
import json
import threading
import hashlib
import platform
import getpass
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API = "https://triage.golproductions.com/preflight"
INSTANT = "https://triage.golproductions.com/instant-key"
CHANNEL = "sublime"
VERSION = "1.0.1"


def get_client_id():
    settings = sublime.load_settings("GOLCheck.sublime-settings")
    return settings.get("client_id", "") or ""


def device_fingerprint():
    # One-way hash of coarse machine facts. No personal data. Used only by the
    # server to rate-limit free-key minting.
    seed = "|".join([
        platform.node() or "",
        platform.system() or "",
        platform.machine() or "",
        getpass.getuser() or "",
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def mint_instant_key():
    # Mint a free key with no signup. Persists it to settings and returns it.
    try:
        body = json.dumps({
            "fingerprint": device_fingerprint(),
            "channel": CHANNEL,
        }).encode("utf-8")
        req = Request(INSTANT, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "sublime/" + VERSION)
        with urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode("utf-8"))
        cid = data.get("client_id")
        if cid:
            settings = sublime.load_settings("GOLCheck.sublime-settings")
            settings.set("client_id", cid)
            sublime.save_settings("GOLCheck.sublime-settings")
            return cid
    except Exception:
        return None
    return None


def validate_async(command, callback):
    def run():
        client_id = get_client_id()
        if not client_id:
            # No key yet: mint one instantly. No email, no browser.
            client_id = mint_instant_key()
            if not client_id:
                callback(None, "Could not activate Check. Check your connection and try again.")
                return

        body = json.dumps({
            "command": command,
            "platform": "sublime",
            "channel": CHANNEL,
            "v": VERSION,
        }).encode("utf-8")

        req = Request(API, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-GOL-CLIENT-ID", client_id)
        req.add_header("User-Agent", "sublime/" + VERSION)

        try:
            with urlopen(req, timeout=5) as res:
                data = json.loads(res.read().decode("utf-8"))
                callback(data.get("verdict"), data.get("reason"))
        except HTTPError as e:
            if e.code == 402:
                try:
                    d = json.loads(e.read().decode("utf-8"))
                    msg = d.get("upgrade") or "Top up at https://www.golproductions.com/console.html"
                except Exception:
                    msg = "Top up at https://www.golproductions.com/console.html"
                callback("invalid", "You've used all 120 free checks for today. Credits never expire. " + msg)
            else:
                callback(None, "Check API error: " + str(e.code))
        except (URLError, json.JSONDecodeError, Exception) as e:
            callback(None, str(e))

    threading.Thread(target=run, daemon=True).start()


class GolCheckValidateCommandCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel(
            "Command to validate:", "", self.on_done, None, None
        )

    def on_done(self, command):
        if not command.strip():
            return
        sublime.status_message("Check: Validating...")
        validate_async(command, self.show_result)

    def show_result(self, verdict, reason):
        def update():
            short = (reason or "")[:80] if reason else ""
            if verdict == "runnable":
                sublime.message_dialog("Check: ✓ Runnable")
            elif verdict:
                sublime.message_dialog("Check: ✗ Blocked — " + (reason or "invalid"))
            else:
                sublime.error_message("Check error: " + (reason or "unknown"))

        sublime.set_timeout(update, 0)


class GolCheckValidateSelectionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        sel = self.view.sel()
        if not sel or sel[0].empty():
            sublime.status_message("Check: No selection")
            return

        command = self.view.substr(sel[0]).strip()
        if not command:
            return

        sublime.status_message("Check: Validating...")
        validate_async(command, self.show_result)

    def show_result(self, verdict, reason):
        def update():
            if verdict == "runnable":
                sublime.status_message("Check: ✓ Runnable")
            elif verdict:
                sublime.status_message("Check: ✗ Blocked — " + (reason or "invalid"))
            else:
                sublime.status_message("Check error: " + (reason or "unknown"))

        sublime.set_timeout(update, 0)

    def is_enabled(self):
        sel = self.view.sel()
        return bool(sel and not sel[0].empty())


class GolCheckSetupCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel(
            "GOL Client ID:", get_client_id(), self.on_done, None, None
        )

    def on_done(self, client_id):
        if client_id.strip():
            settings = sublime.load_settings("GOLCheck.sublime-settings")
            settings.set("client_id", client_id.strip())
            sublime.save_settings("GOLCheck.sublime-settings")
            sublime.status_message("Check: Client ID saved")
