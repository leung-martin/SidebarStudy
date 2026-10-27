"""Two-way sync of flashcard mastery levels with yoyochinese.com.

YoYo has no public API, so this talks to the same private endpoints the
YoYo website uses, signed in with the browser session cookie you paste
into the app. Levels follow YoYo: -1 new, 0-4 learning, 5 mastered.

  pull:  POST /flashcards/manage/cards        (50 cards per page)
  push:  POST /results/flashcard-mastery      (answers per course level)

YoYo is the source of truth. The app never sets levels; it only sends
per-card answers, like finishing a deck on the website: "good" (Not Bad,
+1) when you say it right, "hard" (Forgot, back to 0) when you pass or
slip a tone. So a send can only change the cards you answered. Each push
is checked by reading the course level before and after; if any card you
did not answer lost level, pushing stops for good.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://yoyochinese.com/api/v1"
PAGE_SIZE = 50          # the server caps pages at 50 cards
PULL_WORKERS = 8
UNION_ROUNDS = 12       # re-reads of an oversized group before giving up
FULL_PULL_EVERY = 6 * 3600
CHECK_EVERY = 5 * 60    # how often to ask YoYo whether anything changed there
PUSH_DEBOUNCE = 3       # seconds to wait for more answers before pushing

# YoYo's grade buttons: Forgot (hard) -5, Not Bad (good) +1, Easy +2
LEVEL_DELTA = {"hard": -5, "good": 1, "easy": 2}
MASTERED = 5


def next_level(level, answer):
    return max(0, min(MASTERED, max(level, 0) + LEVEL_DELTA[answer]))


class AuthError(Exception):
    pass


class YoyoClient:
    def __init__(self, cookie):
        self.cookie = cookie.strip()

    def post(self, path, body):
        req = urllib.request.Request(
            API + path, data=json.dumps(body).encode(), method="POST",
            headers={
                "Content-Type": "application/json",
                "is-native": "false",
                "Cookie": self.cookie,
                "Origin": "https://yoyochinese.com",
                "Referer": "https://yoyochinese.com/mastery/flashcards",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SidebarStudy",
            })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError(f"YoYo answered {e.code}") from e
            raise

    def me(self):
        me = (self.post("/me/provider", {}) or {}).get("meData") or {}
        if not me.get("isValid"):
            raise AuthError("not signed in")
        return me

    def cards_page(self, filters, page=1):
        data = self.post("/flashcards/manage/cards",
                         {"filters": filters, "page": page, "cardsPerPage": PAGE_SIZE}) or {}
        if "flashcards" not in data:
            raise AuthError("could not read flashcards")
        return data

    def lesson_filters(self):
        """One card filter per lesson. YoYo's paging has no stable order (the same
        page can return different cards each time), so cards are fetched in groups
        small enough to fit on a single page."""
        opts = self.post("/page/flashcards-mastery-manage_page", {})["pageData"]["filterOptions"]
        units = {u["value"]: u for u in opts["unitOptions"] if u["value"]}
        levels = {lv["value"]: lv for lv in opts["levelOptions"] if lv["value"]}
        out = []
        for lesson in opts["lessonOptions"]:
            unit = units.get(lesson.get("unitId"))
            if not lesson["value"] or not unit:
                continue
            level = levels.get(unit.get("levelId"), {})
            out.append({"masteryType": "all", "courseId": level.get("courseId") or "",
                        "levelId": unit.get("levelId", ""), "unitId": unit["value"],
                        "lessonId": lesson["value"]})
        return out

    def submit(self, level_id, responses):
        return self.post("/results/flashcard-mastery",
                         {"responses": responses, "objectType": "level", "objectId": level_id})


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


class YoyoSync(threading.Thread):
    """Background worker. Talks to the UI only through `events`:
      ("yoyo_status", state)   one of: not signed in, expired, offline, blocked,
                               downloading, out of date, up to date
      ("yoyo_levels", (levels {code: level}, started_at, full))
      ("yoyo_due", (codes due for review, in YoYo's order, fetched_at))
    Every exchange with YoYo is appended to log_file (one JSON line each) as a receipt:
      {"t": epoch, "kind": upload|download|due|check|sign-in|blocked|dropped|error, "text": summary,
       "cards": [{"code", "answer"?, "before", "after"}]}   (cards only where levels changed)
    """

    def __init__(self, events, auth_file, cache_file, log_file=None):
        super().__init__(daemon=True)
        self.events = events
        self.auth_file, self.cache_file, self.log_file = auth_file, cache_file, log_file
        self.last_error = ""
        self.cookie = _load(auth_file, {}).get("cookie", "")
        # cards: code -> {"id", "levelId"}; pending: answers not yet sent to YoYo
        self.cache = _load(cache_file, {})
        self.cache.setdefault("cards", {})
        self.cache.setdefault("pending", [])
        self.cache.setdefault("full_pull", 0)
        self.cache.setdefault("push_blocked", "")  # set if a push ever lowered a level
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.want_full = False
        self.want_due = True  # ask YoYo which cards are due (at startup, and after each round)
        self.lessons = []  # per-lesson card filters, loaded on the first pull
        self.known = {}    # code -> YoYo level as of the last pull
        self.blocked_reason = self.cache["push_blocked"]

    # ---------- called from the UI thread

    @property
    def signed_in(self):
        return bool(self.cookie)

    @property
    def pending_count(self):
        with self.lock:
            return len(self.cache["pending"])

    def set_cookie(self, cookie):
        self.cookie = cookie.strip()
        _save(self.auth_file, {"cookie": self.cookie})
        self.log("sign-in", "Saved a new YoYo session")
        self.sync_now(full=True)

    def record(self, code, answer):
        if answer not in LEVEL_DELTA:
            return
        with self.lock:
            self.cache["pending"].append({"code": code, "answer": answer, "t": int(time.time())})
            _save(self.cache_file, self.cache)
        self.wake.set()

    def sync_now(self, full=False):
        self.want_full = self.want_full or full
        self.wake.set()

    def request_due(self):
        """Send any pending answers, then ask YoYo which cards are due now."""
        self.want_due = True
        self.wake.set()

    # ---------- worker

    def status(self, state):
        self.events.put(("yoyo_status", state))

    def log(self, kind, text, cards=None):
        """Append one timestamped receipt line to the activity log."""
        if not self.log_file:
            return
        entry = {"t": int(time.time()), "kind": kind, "text": text}
        if cards:
            entry["cards"] = cards
        try:
            with self.lock, open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def log_error(self, text):
        if text != self.last_error:  # an outage retries every minute: log it once
            self.log("error", text)
        self.last_error = text

    def run(self):
        self.sync_now(full=True)  # full download at startup (~20 s)
        while True:
            woken = self.wake.wait(timeout=CHECK_EVERY)
            self.wake.clear()
            if woken:
                time.sleep(PUSH_DEBOUNCE)
                self.wake.clear()
            self._cycle(check=not woken)

    def _cycle(self, check=False):
        if not self.cookie:
            self.status("not signed in")
            return
        client = YoyoClient(self.cookie)
        try:
            stale = time.time() - self.cache["full_pull"] > FULL_PULL_EVERY
            if check and not stale and self.known:
                changed = self._changed_on_yoyo(client)
                self.log("check", "Checked YoYo for changes made elsewhere: "
                         + ("found some, downloading" if changed else "none"))
                if changed:
                    self.status("out of date")
                    self.want_full = True
            self._push(client)
            if self.want_due:  # after the push, so cards just answered aren't still listed as due
                self.want_due = False
                fetched = time.time()
                due = self._due(client)
                self.log("due", f"Asked YoYo which cards are due: {len(due)} review(s)")
                self.events.put(("yoyo_due", (due, fetched)))
            if self.want_full or stale or not self.cache["cards"]:
                self.want_full = False
                self._pull(client)
            self.status("blocked" if self.cache["push_blocked"] else "up to date")
            self.last_error = ""
        except AuthError:
            self.log_error("YoYo sign-in expired: nothing sent")
            self.status("expired")
        except Exception as e:  # offline, server error: keep answers and retry later
            self.log_error(f"Couldn't reach YoYo ({type(e).__name__}): answers kept to send later")
            self.status("offline")
            threading.Timer(60, self.wake.set).start()

    def _due(self, client):
        """Cards YoYo's spaced-repetition schedule says are due, exactly as its website
        builds a review deck: each course's level list says how many are due, and each
        level's deck lists them. Both are read-only page requests."""
        opts = client.post("/page/flashcards-mastery-manage_page", {})["pageData"]["filterOptions"]
        due = []
        for course in opts["courseOptions"]:
            cid = course["value"]
            if not cid:
                continue
            decks = client.post("/page/flashcards-mastery-decks_page", {
                "pageUrl": f"/mastery/flashcards/decks/course/{cid}", "objectType": "course", "objectId": cid,
            }).get("pageData", {}).get("decks", [])
            for deck in decks:
                if not deck.get("totalDue") or deck.get("isLocked"):
                    continue
                lid = deck["levelId"]
                cards = client.post("/page/flashcards-mastery-deck_page", {
                    "pageUrl": f"/mastery/flashcards/level/{lid}", "objectType": "level", "objectId": lid,
                }).get("pageData", {}).get("flashcards", [])
                due += [c["code"] for c in cards if c.get("masteryLevel", -1) >= 0]  # reviews, not new
        return due

    def _changed_on_yoyo(self, client):
        """Cheap check (one request): has your mastered count on YoYo moved since the
        last download, e.g. because you studied on the website?"""
        mastered = sum(1 for lv in self.known.values() if lv >= MASTERED)
        return client.me().get("masteredFlashcards") != mastered

    def _fetch_group(self, client, filters):
        """Every card matching filters, despite YoYo's unstable paging."""
        first = client.cards_page(filters)
        total = first.get("totalFlashcards", 0)
        if total <= PAGE_SIZE:
            return first["flashcards"]
        if filters["masteryType"] == "all":  # split the group so each half may fit one page
            return [c for kind in ("mastered", "not-mastered")
                    for c in self._fetch_group(client, {**filters, "masteryType": kind})]
        seen = {c["_id"]: c for c in first["flashcards"]}
        pages = -(-total // PAGE_SIZE)
        for _ in range(UNION_ROUNDS):  # re-read every page until all cards have turned up
            for p in range(1, pages + 1):
                for c in client.cards_page(filters, p)["flashcards"]:
                    seen[c["_id"]] = c
            if len(seen) >= total:
                return list(seen.values())
        raise RuntimeError(f"could only read {len(seen)} of {total} cards in a lesson")

    def _fetch(self, client, level_id=""):
        """All cards (or one course level's cards) with their YoYo levels, read lesson by lesson."""
        if not self.lessons:
            self.lessons = client.lesson_filters()
        groups = [f for f in self.lessons if not level_id or f["levelId"] == level_id]
        with ThreadPoolExecutor(PULL_WORKERS) as pool:
            results = list(pool.map(lambda f: self._fetch_group(client, f), groups))
        return list({c["_id"]: c for group in results for c in group}.values())

    def _pull(self, client, level_id="", cards=None):
        started = time.time()
        if cards is None:
            if not level_id:
                self.status("downloading")
                self.lessons = []  # refresh the lesson list on every full pull
            cards = self._fetch(client, level_id)
        levels = {}
        if not level_id:  # a full download: note what changed on YoYo since the last one
            changed = [{"code": c["code"], "before": self.known[c["code"]], "after": c.get("masteryLevel", -1)}
                       for c in cards if c.get("code") in self.known
                       and self.known[c["code"]] != c.get("masteryLevel", -1)]
            mastered = sum(1 for c in cards if c.get("masteryLevel", -1) >= MASTERED)
            self.log("download", f"Downloaded {len(cards)} cards' levels ({mastered} mastered)"
                     + (f"; {len(changed)} changed since the last download" if changed else ""), changed[:300])
        with self.lock:
            for c in cards:
                code = c.get("code")
                if code:
                    self.cache["cards"][code] = {"id": c["_id"], "levelId": c.get("levelId", "")}
                    levels[code] = c.get("masteryLevel", -1)
            self.known.update(levels)
            if not level_id:
                self.cache["full_pull"] = int(started)
            _save(self.cache_file, self.cache)
        self.events.put(("yoyo_levels", (levels, started, not level_id)))

    def _push(self, client):
        """Send pending answers grouped by course level, checking each send."""
        if self.cache["push_blocked"]:
            return
        with self.lock:
            pending = list(self.cache["pending"])
        by_level, unknown = {}, []
        for item in pending:
            card = self.cache["cards"].get(item["code"])
            if card and card.get("levelId"):
                by_level.setdefault(card["levelId"], []).append((item, card["id"]))
            elif item["t"] < self.cache["full_pull"]:
                unknown.append(item)  # a full pull since the answer didn't find it on YoYo: drop it
        for level_id, items in by_level.items():
            before = {c["code"]: c.get("masteryLevel", -1) for c in self._fetch(client, level_id)}
            elsewhere = [{"code": code, "before": self.known[code], "after": lv} for code, lv in before.items()
                         if code in self.known and self.known[code] != lv]
            if elsewhere:  # e.g. studied on the website: receipt it before this send absorbs it
                self.log("download", f"Noticed {len(elsewhere)} card(s) changed on YoYo since the last download",
                         elsewhere)
            client.submit(level_id, [{"flashcardId": fid, "answer": it["answer"]} for it, fid in items])
            with self.lock:
                self.cache["pending"] = [p for p in self.cache["pending"]
                                         if not any(p is it for it, _ in items)]
                _save(self.cache_file, self.cache)
            after_cards = self._fetch(client, level_id)
            answered = {it["code"] for it, _ in items}
            dropped = [code for code, lv in ((c["code"], c.get("masteryLevel", -1)) for c in after_cards)
                       if code not in answered and lv < before.get(code, -1)]
            after = {c["code"]: c.get("masteryLevel", -1) for c in after_cards}
            names = {"easy": "Easy", "good": "Not Bad", "hard": "Forgot"}
            counts = [f"{n} {names[a]}" for a in ("easy", "good", "hard")
                      if (n := sum(1 for it, _ in items if it["answer"] == a))]
            self.log("upload", f"Sent {len(items)} answer(s): " + ", ".join(counts),
                     [{"code": it["code"], "answer": it["answer"], "before": before.get(it["code"], -1),
                       "after": after.get(it["code"], -1)} for it, _ in items])
            self._pull(client, level_id, after_cards)
            if dropped:
                with self.lock:
                    self.cache["push_blocked"] = (f"{len(dropped)} card(s) you didn't answer lost level "
                                                  f"({', '.join(dropped[:5])})")
                    _save(self.cache_file, self.cache)
                self.log("blocked", "Uploads stopped: " + self.cache["push_blocked"],
                         [{"code": c, "before": before.get(c, -1), "after": after.get(c, -1)} for c in dropped])
                return
        if unknown:
            self.log("dropped", f"Dropped {len(unknown)} answer(s) for cards YoYo doesn't have",
                     [{"code": it["code"], "answer": it["answer"]} for it in unknown])
            with self.lock:
                self.cache["pending"] = [p for p in self.cache["pending"] if p not in unknown]
                _save(self.cache_file, self.cache)
