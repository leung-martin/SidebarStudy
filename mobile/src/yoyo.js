// Two-way sync with yoyochinese.com — a port of the PC app's yoyo_sync.py.
//
// YoYo has no public API; this uses the same private endpoints its website uses, signed in
// with your YoYo email + password (the session token is renewed automatically when it expires).
// Levels follow YoYo: -1 new, 0-4 learning, 5 mastered. YoYo is the source of truth: the app
// only sends per-card answers (Easy +2 / Not Bad +1 / Forgot back to 0), and every send is
// checked by reading the course level before and after — if a card you did NOT answer lost
// level, sending stops for good.

const API = "https://yoyochinese.com/api/v1";
const PAGE_SIZE = 50;           // the server caps pages at 50 cards
const WORKERS = 8;
const UNION_ROUNDS = 12;        // re-reads of an oversized group (paging order is unstable)
const FULL_PULL_EVERY = 6 * 3600e3;
const CHECK_EVERY = 5 * 60e3;   // how often to ask YoYo if anything changed there
const PUSH_DEBOUNCE = 3e3;

export const LEVEL_DELTA = { hard: -5, good: 1, easy: 2 };
export const MASTERED = 5;
export const nextLevel = (level, answer) => Math.max(0, Math.min(MASTERED, Math.max(level, 0) + LEVEL_DELTA[answer]));

export class AuthError extends Error {}

async function pool(items, worker, size = WORKERS) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await worker(items[i]); }
  }));
  return out;
}

/**
 * http(method, url, headers, body) -> {status, data, headers}: CapacitorHttp in the app,
 * fetch in tests. store: {get(key), set(key, value)} persisting JSON-able values.
 * events: {status(state), levels(levels, startedAt, full), due(codes, fetchedAt)}.
 */
export class YoyoSync {
  constructor(http, store, events) {
    this.http = http;
    this.store = store;
    this.events = events;
    this.token = store.get("yoyo_token") || "";
    this.cache = store.get("yoyo_cache") || {};
    this.cache.cards ??= {};          // code -> {id, levelId}
    this.cache.pending ??= [];        // answers not yet sent
    this.cache.fullPull ??= 0;
    this.cache.pushBlocked ??= "";
    this.lessons = [];
    this.known = {};                  // code -> YoYo level as of the last pull
    this.wantFull = true;
    this.wantDue = true;
    this.running = false;
    this.timer = null;
    this.credentials = null;          // {email, password}, set by the app from secure storage
  }

  // ---------- called by the app

  get signedIn() { return !!(this.token || this.credentials); }
  get pendingCount() { return this.cache.pending.length; }
  get blocked() { return this.cache.pushBlocked; }

  setCredentials(creds) {
    this.credentials = creds;
    this.token = "";
    this.store.set("yoyo_token", "");
    this.syncNow(true);
  }

  signOut() {
    this.credentials = null;
    this.token = "";
    this.store.set("yoyo_token", "");
    this.events.status("not signed in");
  }

  record(code, answer) {
    if (!(answer in LEVEL_DELTA)) return;
    this.cache.pending.push({ code, answer, t: Date.now() });
    this.saveCache();
    this.schedule(PUSH_DEBOUNCE);
  }

  syncNow(full = false) { this.wantFull ||= full; this.schedule(0); }
  requestDue() { this.wantDue = true; this.schedule(PUSH_DEBOUNCE); }

  start() {
    this.schedule(0);
    setInterval(() => this.schedule(0, true), CHECK_EVERY);
  }

  // ---------- worker

  saveCache() { this.store.set("yoyo_cache", this.cache); }

  schedule(delay, check = false) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.cycle(check), delay);
  }

  async cycle(check = false) {
    if (this.running) { this.schedule(1000); return; }
    if (!this.signedIn) { this.events.status("not signed in"); return; }
    this.running = true;
    try {
      const stale = Date.now() - this.cache.fullPull > FULL_PULL_EVERY;
      if (check && !stale && Object.keys(this.known).length && await this.changedOnYoyo()) {
        this.events.status("out of date");
        this.wantFull = true;
      }
      await this.push();
      if (this.wantDue) {  // after the push, so cards just answered aren't still listed as due
        this.wantDue = false;
        const fetched = Date.now();
        this.events.due(await this.due(), fetched);
      }
      if (this.wantFull || stale || !Object.keys(this.cache.cards).length) {
        this.wantFull = false;
        await this.pull();
      }
      this.events.status(this.cache.pushBlocked ? "blocked" : "up to date");
    } catch (e) {
      if (e instanceof AuthError) this.events.status("expired");
      else { this.events.status("offline"); this.schedule(60e3); }
      console.warn("sync failed", e);
    } finally {
      this.running = false;
    }
  }

  // ---------- HTTP

  async login() {
    if (!this.credentials) throw new AuthError("not signed in");
    const res = await this.http("POST", `${API}/auth/login`, { "Content-Type": "application/json", "is-native": "false" },
      { email: this.credentials.email, password: this.credentials.password });
    if (res.status >= 400) throw new AuthError(`sign-in failed (${res.status})`);
    // The session is the signed "token" cookie (s%3A<jwt>.<signature>). The body's plain token
    // is for YoYo's own app and isn't accepted for page data, so the cookie comes first.
    const raw = Object.entries(res.headers || {}).filter(([k]) => k.toLowerCase() === "set-cookie").map(([, v]) => v).join(";");
    const token = (raw.match(/(?:^|[;,\s])token=([^;,\s]+)/) || [])[1] || "";
    if (!token) throw new AuthError("sign-in gave no session");
    this.token = token;
    this.store.set("yoyo_token", token);
  }

  async post(path, body, retried = false) {
    if (!this.token) await this.login();
    const res = await this.http("POST", API + path, {
      "Content-Type": "application/json", "is-native": "false", Cookie: `token=${this.token}`,
    }, body);
    // signed-out requests don't always fail: pages come back empty and me/provider says !isValid
    const emptyPage = path.startsWith("/page/") && res.status === 200 && res.data?.pageData
      && !Object.keys(res.data.pageData).length;
    const expired = res.status === 401 || res.status === 403 || emptyPage
      || (path === "/me/provider" && res.data?.meData && !res.data.meData.isValid);
    if (expired) {
      if (retried || !this.credentials) throw new AuthError(`YoYo answered ${res.status}`);
      this.token = "";
      await this.login();             // session expired: sign in again with the saved password
      return this.post(path, body, true);
    }
    if (res.status >= 400) throw new Error(`YoYo answered ${res.status} for ${path}`);
    return res.data;
  }

  async me() {
    const me = (await this.post("/me/provider", {}))?.meData || {};
    if (!me.isValid) throw new AuthError("not signed in");
    return me;
  }

  async cardsPage(filters, page = 1) {
    const data = await this.post("/flashcards/manage/cards", { filters, page, cardsPerPage: PAGE_SIZE });
    if (!data || !("flashcards" in data)) throw new AuthError("could not read flashcards");
    return data;
  }

  /** One card filter per lesson: groups small enough for a single page (paging is unstable). */
  async lessonFilters() {
    const opts = (await this.post("/page/flashcards-mastery-manage_page", {})).pageData.filterOptions;
    const units = Object.fromEntries(opts.unitOptions.filter(u => u.value).map(u => [u.value, u]));
    const levels = Object.fromEntries(opts.levelOptions.filter(l => l.value).map(l => [l.value, l]));
    const out = [];
    for (const lesson of opts.lessonOptions) {
      const unit = units[lesson.unitId];
      if (!lesson.value || !unit) continue;
      const level = levels[unit.levelId] || {};
      out.push({ masteryType: "all", courseId: level.courseId || "", levelId: unit.levelId || "",
        unitId: unit.value, lessonId: lesson.value });
    }
    return out;
  }

  async fetchGroup(filters) {
    const first = await this.cardsPage(filters);
    const total = first.totalFlashcards || 0;
    if (total <= PAGE_SIZE) return first.flashcards;
    if (filters.masteryType === "all") {  // split the group so each half may fit one page
      const halves = await Promise.all(["mastered", "not-mastered"].map(k => this.fetchGroup({ ...filters, masteryType: k })));
      return halves.flat();
    }
    const seen = new Map(first.flashcards.map(c => [c._id, c]));
    const pages = Math.ceil(total / PAGE_SIZE);
    for (let round = 0; round < UNION_ROUNDS; round++) {  // re-read until every card turned up
      for (let p = 1; p <= pages; p++) for (const c of (await this.cardsPage(filters, p)).flashcards) seen.set(c._id, c);
      if (seen.size >= total) return [...seen.values()];
    }
    throw new Error(`could only read ${seen.size} of ${total} cards in a lesson`);
  }

  /** All cards (or one course level's) with their YoYo levels, read lesson by lesson. */
  async fetch(levelId = "") {
    if (!this.lessons.length) this.lessons = await this.lessonFilters();
    const groups = this.lessons.filter(f => !levelId || f.levelId === levelId);
    const results = await pool(groups, f => this.fetchGroup(f));
    return [...new Map(results.flat().map(c => [c._id, c])).values()];
  }

  async pull(levelId = "", cards = null) {
    const started = Date.now();
    if (!cards) {
      if (!levelId) { this.events.status("downloading"); this.lessons = []; }
      cards = await this.fetch(levelId);
    }
    const levels = {};
    for (const c of cards) {
      if (!c.code) continue;
      this.cache.cards[c.code] = { id: c._id, levelId: c.levelId || "" };
      levels[c.code] = c.masteryLevel ?? -1;
    }
    Object.assign(this.known, levels);
    if (!levelId) this.cache.fullPull = started;
    this.saveCache();
    this.events.levels(levels, started, !levelId);
  }

  async push() {
    if (this.cache.pushBlocked) return;
    const byLevel = new Map(), unknown = [];
    for (const item of this.cache.pending) {
      const card = this.cache.cards[item.code];
      if (card?.levelId) {
        if (!byLevel.has(card.levelId)) byLevel.set(card.levelId, []);
        byLevel.get(card.levelId).push([item, card.id]);
      } else if (item.t < this.cache.fullPull) {
        unknown.push(item);  // a full pull since the answer didn't find it on YoYo
      }
    }
    for (const [levelId, items] of byLevel) {
      const before = Object.fromEntries((await this.fetch(levelId)).map(c => [c.code, c.masteryLevel ?? -1]));
      await this.post("/results/flashcard-mastery", {
        responses: items.map(([it, id]) => ({ flashcardId: id, answer: it.answer })),
        objectType: "level", objectId: levelId,
      });
      const sent = new Set(items.map(([it]) => it));
      this.cache.pending = this.cache.pending.filter(p => !sent.has(p));
      this.saveCache();
      const after = await this.fetch(levelId);
      const answered = new Set(items.map(([it]) => it.code));
      const dropped = after.filter(c => !answered.has(c.code) && (c.masteryLevel ?? -1) < (before[c.code] ?? -1)).map(c => c.code);
      await this.pull(levelId, after);
      if (dropped.length) {
        this.cache.pushBlocked = `${dropped.length} card(s) you didn't answer lost level (${dropped.slice(0, 5).join(", ")})`;
        this.saveCache();
        return;
      }
    }
    if (unknown.length) {
      this.cache.pending = this.cache.pending.filter(p => !unknown.includes(p));
      this.saveCache();
    }
  }

  /** Cards YoYo's spaced-repetition schedule says are due, the way its website builds a review deck. */
  async due() {
    const opts = (await this.post("/page/flashcards-mastery-manage_page", {})).pageData.filterOptions;
    const due = [];
    for (const course of opts.courseOptions) {
      if (!course.value) continue;
      const cid = course.value;
      const decks = (await this.post("/page/flashcards-mastery-decks_page", {
        pageUrl: `/mastery/flashcards/decks/course/${cid}`, objectType: "course", objectId: cid,
      }))?.pageData?.decks || [];
      for (const deck of decks) {
        if (!deck.totalDue || deck.isLocked) continue;
        const lid = deck.levelId;
        const cards = (await this.post("/page/flashcards-mastery-deck_page", {
          pageUrl: `/mastery/flashcards/level/${lid}`, objectType: "level", objectId: lid,
        }))?.pageData?.flashcards || [];
        due.push(...cards.filter(c => (c.masteryLevel ?? -1) >= 0).map(c => c.code));  // reviews, not new
      }
    }
    return due;
  }

  /** Cheap check: has your mastered count on YoYo moved since the last download? */
  async changedOnYoyo() {
    const mastered = Object.values(this.known).filter(l => l >= MASTERED).length;
    return (await this.me()).masteredFlashcards !== mastered;
  }
}
