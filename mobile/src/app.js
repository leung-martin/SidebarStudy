// Sidebar Study for Android: the PC app's card flow, grading and YoYo sync, on a phone.
import { Capacitor, CapacitorHttp } from "@capacitor/core";
import { TextToSpeech } from "@capacitor-community/text-to-speech";
import { KeepAwake } from "@capacitor-community/keep-awake";
import { Haptics, NotificationType } from "@capacitor/haptics";
import { SecureStoragePlugin } from "capacitor-secure-storage-plugin";
import { checkBest, saidRow, isPass, isHanzi, stripHanzi, toneName, answerRuby, cardUnits } from "./grading.js";
import { YoyoSync, nextLevel, MASTERED } from "./yoyo.js";
import { Listener } from "./speech.js";

const DECKS = [  // course order, like the PC app
  ["yoyochinese_beginner_conversational_flashcards.json", "Beginner Conversational"],
  ["yoyochinese_intermediate_conversational_flashcards.json", "Intermediate Conversational"],
  ["yoyochinese_upper_intermediate_conversational_flashcards.json", "Upper Intermediate Conversational"],
  ["yoyochinese_advanced_conversational_chinese_flashcards.json", "Advanced Conversational Chinese"],
  ["yoyochinese_chinese_characters_flashcards.json", "Chinese Characters"],
  ["yoyochinese_chinese_characters_ii_flashcards.json", "Chinese Characters II"],
  ["yoyochinese_chinese_character_reader_flashcards.json", "Chinese Character Reader"],
];
const NEW_PER_ROUND = 25;     // new cards per round, like a YoYo deck
const DUE_WAIT_MS = 30e3;     // wait this long for YoYo's due list before using the offline order
const $ = id => document.getElementById(id);
const native = Capacitor.isNativePlatform();

// ---------- storage (localStorage; the YoYo password goes to Android's secure storage)

const store = {
  get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};
const settings = Object.assign({
  deck: "All decks", delay: 3, showEnglish: false, reviewMastered: false, keepAwake: true,
  micMode: "tap", listenWindow: 60, sleepMin: 2, email: "",
}, store.get("settings") || {});
const saveSettings = () => store.set("settings", settings);

const secure = {
  async get(key) { try { return (await SecureStoragePlugin.get({ key })).value; } catch { return null; } },
  async set(key, value) { await SecureStoragePlugin.set({ key, value }); },
  async remove(key) { try { await SecureStoragePlugin.remove({ key }); } catch { /* not there */ } },
};

// ---------- state

const S = {
  decks: {}, allCards: {}, progress: store.get("progress") || {},
  queue: [], card: null, locked: false, solved: false, mustRepeat: false, helped: false,
  said: null, revealArgs: null, passedAt: 0, advanceTimer: null,
  due: null, dueTime: 0, roundRequested: false, speaking: false,
  streak: 0, right: 0, passed: 0, syncState: "", hzArgs: null,
};

// ---------- YoYo

async function http(method, url, headers, body) {
  if (!native) {  // browser preview: no CORS bypass, so YoYo can't be reached
    throw new Error("YoYo sync only works in the Android app");
  }
  const res = await CapacitorHttp.request({ url, method, headers, data: body, responseType: "json" });
  let data = res.data;
  if (typeof data === "string") { try { data = JSON.parse(data); } catch { /* keep text */ } }
  return { status: res.status, data, headers: res.headers };
}

const sync = new YoyoSync(http, store, {
  status: state => { S.syncState = state; showSyncStatus(); if (["offline", "expired", "not signed in"].includes(state)) dueFallback(state); },
  levels: (levels, started, full) => applyLevels(levels, started, full),
  due: (codes, fetched) => applyDue(codes, fetched),
});

// ---------- cards and levels

const level = code => S.progress[code]?.level ?? -1;
const isMastered = code => level(code) >= MASTERED;
const inProgress = code => {
  const p = S.progress[code] || {};
  return ((p.level ?? -1) >= 0 || p.ok || p.fail) && (p.level ?? -1) < MASTERED;
};
const deckCards = () => S.decks[settings.deck] || Object.values(S.decks).flat();
const lessonOf = c => c.code.split("-").slice(0, -1).join("-");  // ICC-011-01-004 -> ICC-011-01

function shuffle(a) { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }
function shuffledGroups(items, key, sortKeys) {
  const groups = new Map();
  for (const c of items) { const k = key(c); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(c); }
  const keys = sortKeys ? [...groups.keys()].sort((a, b) => a - b) : [...groups.keys()];
  return keys.flatMap(k => shuffle(groups.get(k)));
}

/** With YoYo: its due reviews, then new cards from your current level in lesson order.
 *  Offline: cards you've started (lowest level first), then new cards in course order. */
function rebuildQueue() {
  const cards = deckCards();
  const current = S.card?.code;
  let queue;
  if (S.due) {
    const inDeck = new Map(cards.map(c => [c.code, c]));
    const answered = new Set(Object.entries(S.progress).filter(([, p]) => (p.last || 0) > S.dueTime).map(([k]) => k));
    const due = [...new Set(S.due)].filter(k => inDeck.has(k) && !answered.has(k)).map(k => inDeck.get(k));
    const fresh = cards.filter(c => level(c.code) < 0);
    const levelOf = c => sync.cache.cards[c.code]?.levelId || `${c.code.split("-")[0]}|${c.level}`;
    const focus = fresh.length ? levelOf(fresh[0]) : null;  // one level at a time keeps you on a unit
    queue = [...due, ...shuffledGroups(fresh.filter(c => levelOf(c) === focus), lessonOf).slice(0, NEW_PER_ROUND)];
  } else {
    const started = cards.filter(c => inProgress(c.code));
    const fresh = cards.filter(c => !isMastered(c.code) && !inProgress(c.code));
    queue = [...shuffledGroups(started, c => Math.max(level(c.code), 0), true), ...shuffledGroups(fresh, lessonOf)];
  }
  if (settings.reviewMastered) queue.push(...shuffle(cards.filter(c => isMastered(c.code))));
  S.queue = queue.filter(c => c.code !== current);
}

function applyLevels(levels, started, full) {
  let changed = false;
  for (const [code, lv] of Object.entries(levels)) {
    if (!S.allCards[code]) continue;
    const p = S.progress[code] ??= { level: -1, ok: 0, fail: 0 };
    if ((p.last || 0) > started || p.level === lv) continue;
    p.level = lv;
    changed = true;
  }
  if (!changed) return;
  store.set("progress", S.progress);
  if (full && !S.due) rebuildQueue();
  updateStats();
  if (S.card && !S.locked) drawLevel(level(S.card.code));
}

function applyDue(codes, fetched) {
  S.due = codes;
  S.dueTime = fetched;
  rebuildQueue();
  if (!S.card) { if (S.queue.length) nextCard(); else showCard(); }
}

function dueFallback(reason) {
  if (S.due || S.card) return;
  S.roundRequested = true;  // don't keep asking; Sync now or the next round will
  rebuildQueue();
  nextCard();
  if (S.card) setStatus(`Using the offline order (${reason})`, "orange");
}

function record(ok, easy = false) {
  const answer = ok ? (easy ? "easy" : "good") : "hard";
  const p = S.progress[S.card.code] ??= { level: -1, ok: 0, fail: 0 };
  p[ok ? "ok" : "fail"]++;
  p.last = Date.now();
  p.level = nextLevel(p.level ?? -1, answer);
  store.set("progress", S.progress);
  sync.record(S.card.code, answer);
  showSyncStatus();
  drawLevel(p.level);
}

// ---------- card flow

function nextCard() {
  clearTimeout(S.advanceTimer);
  Object.assign(S, { locked: false, solved: false, mustRepeat: false, helped: false, said: null, revealArgs: null });
  if (!S.queue.length && sync.signedIn && !S.roundRequested) {
    // round finished (or just started): send the answers, then ask YoYo what's due now
    S.roundRequested = true;
    const finished = S.card !== null;
    S.card = null;
    showLoading(finished ? "Round done ✓ — asking YoYo what's due next…" : "Asking YoYo which cards are due…");
    sync.requestDue();
    return;
  }
  S.card = S.queue.shift() || null;
  if (S.card) S.roundRequested = false;
  listener.restart();
  showCard();
}

function showCard() {
  updateStats();
  const c = S.card;
  if (!c) {
    renderHz({ text: "🎉" });
    $("levelBar").innerHTML = "";
    $("english").textContent = "All caught up — nothing due on YoYo and no new cards left here. Pick another deck, turn on “Review mastered”, or come back later.";
    setStatus("");
    return;
  }
  renderHz({});
  drawLevel(level(c.code));
  $("english").textContent = settings.showEnglish ? c.english : "";
  promptStatus();
}

function promptStatus() {
  if (!S.card || S.locked || S.solved) return;
  setStatus(listener.open ? "🎙 Listening — say it in Mandarin, or say “pass”"
    : "Tap the card and say it in Mandarin — or say “pass”");
}

function finish(ok, message) {
  S.solved = true;
  $("english").textContent = S.card.english;
  if (!ok) {  // passed: reveal, and stay until it's said correctly (YoYo decides when it's due again)
    record(false);
    S.mustRepeat = true;
    renderHz({ ruby: answerRuby(S.card), rubyClass: "pinkt", reveal: true, said: S.said });
    setStatus(message, "pink");
    S.streak = 0;
    S.passed++;
    updateStats();
    animate("flash-pink", "shake");
    haptic(NotificationType.Error);
    return;
  }
  S.locked = true;
  if (!S.mustRepeat) {  // repeating a passed card doesn't count as mastering it
    const easy = !S.helped;
    record(true, easy);
    message += easy ? "   · Easy +2" : "   · Not Bad +1";
    S.streak++;
    S.right++;
    if (S.streak % 5 === 0) message += `   🔥 ${S.streak} in a row!`;
  }
  const ans = answerRuby(S.card);
  renderHz({ matched: new Set(ans.keys()), ruby: ans, rubyClass: "ok", reveal: true, said: S.said });
  setStatus(message, "green");
  updateStats();
  animate("flash-green", "pop");
  haptic(NotificationType.Success);
  S.advanceTimer = setTimeout(nextCard, settings.delay * 1000);
}

function toneSlip(result) {
  // right sounds, wrong tone: not a miss — you just say it again (nothing recorded yet)
  S.solved = true;
  S.helped = true;
  $("english").textContent = S.card.english;
  const ans = answerRuby(S.card);
  renderHz({ matched: new Set(ans.keys()), ruby: ans, rubyClass: "ok", toneOff: result.toneOff, reveal: true, said: S.said });
  const chars = [...stripHanzi(S.card.simplified)];
  const slips = [...result.toneOff].sort((a, b) => a - b).map(i =>
    `${chars[i]} ${ans.get(i)} — ${toneName(ans.get(i))} tone (you said ${toneName(result.ruby.get(i) || "", false)})`);
  setStatus(`Tone off — say it again\n${slips.join("\n")}`, "yellow");
  animate("flash-orange", "shake");
}

function pass() {
  if (!S.card) return;
  if (S.locked || S.mustRepeat) {  // already passed or answered: Pass moves on
    if (Date.now() - S.passedAt > 1000) nextCard();  // a double tap shouldn't skip the answer
    return;
  }
  S.passedAt = Date.now();
  finish(false, "Passed ✗ — say it to continue, or Pass again to skip");
}

function skip() {
  if (S.card && !S.mustRepeat) S.queue.push(S.card);
  nextCard();
}

function gotIt() {
  if (!S.card || S.locked) return;
  S.helped = true;  // unverified by the recognizer, so never Easy
  finish(true, S.mustRepeat ? "✓ Moving on" : "✓ Marked correct");
}

// ---------- hearing results

function onPartial(matches) {
  if (S.locked || !S.card || S.speaking) return;
  const r = checkBest(S.card, matches);
  if (!r) return;
  if (r.ok && r.toneOk) {
    S.said = saidRow(S.card, r);
    finish(true, `✓ ${stripHanzi(r.text)}  ${r.heardPy}`);
    listener.restart();
    return;
  }
  if (!S.solved) renderHz({ matched: r.matched, ruby: r.ruby, toneOff: r.toneOff });
  setStatus(`… ${stripHanzi(r.text) || r.text}  ${r.heardPy}`, "violet");
}

function onFinal(matches) {
  if (S.locked || !S.card || S.speaking) return;
  if (isPass(matches)) { pass(); return; }
  const r = checkBest(S.card, matches);
  if (!r) return;
  S.said = saidRow(S.card, r);
  const shown = `${stripHanzi(r.text) || r.text}  ${r.heardPy}`;
  if (r.ok && !r.toneOk) { toneSlip(r); return; }
  if (r.ok) { finish(true, `✓ ${shown}`); return; }
  S.helped = true;  // wrong guesses cost nothing, but it's no longer a first try
  if (!S.solved) renderHz({ matched: r.matched, final: true, ruby: r.ruby, toneOff: r.toneOff });
  else renderHz({ ...S.revealArgs, reveal: true, said: S.said });  // keep the answer up, attempt under it
  setStatus(`heard ${shown} — try again`, "orange");
  animate("flash-orange", "shake");
}

// ---------- read aloud

async function speak() {
  if (!S.card) return;
  if (!S.locked) S.helped = true;  // a hint, so no Easy
  S.speaking = true;
  $("hear").textContent = "🔊 …";
  await listener.pause();  // the mic mustn't grade the app's own voice
  try {
    await TextToSpeech.speak({ text: stripHanzi(S.card.simplified) || S.card.simplified, lang: "zh-CN", rate: 0.85 });
  } catch (e) {
    setStatus("No Chinese voice: install one in Settings → General management → Text-to-speech.", "red");
  }
  $("hear").textContent = "🔊 Hear";
  setTimeout(() => { S.speaking = false; listener.resume(); }, 600);  // echo tail
}

// ---------- drawing

function renderHz({ text, matched = new Set(), final = false, ruby = new Map(), rubyClass = "", toneOff = new Set(), reveal = false, said = null }) {
  if (reveal) S.revealArgs = { matched, ruby, rubyClass, toneOff };
  const t = text ?? S.card.simplified;
  S.hzArgs = arguments[0];
  const compare = reveal && said;
  const esc = s => s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  let html = compare ? `<div class="col labels"><span class="py">🎯</span><span class="ch">&nbsp;</span><span class="sd">🎙</span></div>` : "";
  let k = 0;
  const parts = t.match(/[A-Za-z]+|./gsu) || [];  // keep English words (John, APP) in one piece
  for (const ch of parts) {
    if (ch.length === 1 && isHanzi(ch)) {
      let cls = matched.has(k) ? "ok" : final ? "bad" : "";
      let pcls = rubyClass || (matched.has(k) ? "ok" : final ? "bad" : "dimt");
      if (toneOff.has(k)) { cls = "tone"; if (!compare) pcls = "tone"; }
      let sd = "";
      if (compare) {
        const [s, state] = said.get(k) || ["—", "bad"];
        sd = `<span class="sd ${state}">${state === "ok" ? "✓" : esc(s)}</span>`;
      }
      html += `<div class="col"><span class="py ${pcls}">${esc(ruby.get(k) || "")}</span><span class="ch ${cls}">${ch}</span>${sd}</div>`;
      k++;
    } else {
      html += `<div class="col"><span class="py"></span><span class="ch">${esc(ch)}</span>${compare ? '<span class="sd"></span>' : ""}</div>`;
    }
  }
  const hz = $("hz");
  hz.className = "hz" + (reveal ? " reveal" : "");
  hz.innerHTML = html;
  fitHz();
}

/** Biggest font that fits the space (the pop animation uses transforms, so it never re-wraps). */
function fitHz() {
  const hz = $("hz"), box = hz.parentElement;
  const n = Math.max(1, hz.children.length);
  let size = Math.min(72, Math.max(20, box.clientWidth / (n * 1.1)));
  hz.style.fontSize = size + "px";
  while (size > 18 && (hz.scrollWidth > box.clientWidth || hz.scrollHeight > box.clientHeight)) {
    size -= 3;
    hz.style.fontSize = size + "px";
  }
}

function drawLevel(lv) {
  const bar = $("levelBar");
  if (lv < 0) { bar.innerHTML = '<span class="new">NEW</span>'; return; }
  const color = lv > 3 ? "var(--green)" : lv > 1 ? "var(--orange)" : "var(--red)";
  bar.innerHTML = [0, 1, 2, 3, 4].map(i => `<i style="${i < lv ? `background:${color}` : ""}"></i>`).join("");
}

function setStatus(text, color = "") {
  const el = $("status");
  el.textContent = text;
  el.style.color = color ? `var(--${color})` : "";
}

function showLoading(text) {
  renderHz({ text: "⏳" });
  $("levelBar").innerHTML = "";
  $("english").textContent = "";
  setStatus(text);
}

function animate(...classes) {
  for (const cls of classes) {
    const el = cls.startsWith("flash") ? $("card") : $("hz");
    el.classList.remove(cls);
    void el.offsetWidth;  // restart the animation
    el.classList.add(cls);
    el.addEventListener("animationend", () => el.classList.remove(cls), { once: true });
  }
}

const haptic = type => { if (native) Haptics.notification({ type }).catch(() => {}); };

function updateStats() {
  const cards = deckCards();
  const done = cards.filter(c => isMastered(c.code)).length;
  $("count").textContent = `${done.toLocaleString()} / ${cards.length.toLocaleString()}`;
  $("progressFill").style.width = `calc((100% - 110px) * ${done / Math.max(1, cards.length)})`;
  $("score").textContent = `✓ ${S.right}  ✗ ${S.passed}`;
  $("streak").textContent = S.streak ? `🔥 ${S.streak}` : "";
}

function showSyncStatus() {
  const n = sync.pendingCount;
  const upload = n ? `↑ ${n} to upload` : "";
  const fixed = {
    "not signed in": ["☁ sign in", "red"], expired: ["☁ sign-in failed", "red"], blocked: ["⚠ uploads stopped", "red"],
    downloading: ["↓ downloading…", "dim"], "out of date": ["↓ out of date", "orange"], checking: ["☁ checking…", "dim"],
  };
  let [text, color] = fixed[S.syncState] || (S.syncState === "offline" ? [`☁ offline${upload ? " · " + upload : ""}`, "orange"]
    : upload ? [upload, "orange"] : ["✓ up to date", "green"]);
  if (!S.syncState) [text, color] = ["☁ …", "dim"];
  const el = $("sync");
  el.textContent = text;
  el.style.color = `var(--${color})`;
  $("accountState").textContent = sync.signedIn
    ? `Signed in as ${settings.email || "you"} · ${text}${sync.blocked ? " — " + sync.blocked : ""}` : "Not signed in";
}

// ---------- listening

const listener = new Listener({
  partial: onPartial,
  final: onFinal,
  error: msg => setStatus(msg, "red"),
  state: ({ listening }) => {
    $("mic").classList.toggle("on", listening);
    $("card").classList.toggle("listening", listening);
    $("micLabel").textContent = listening ? "Listening — tap to stop"
      : settings.micMode === "always" ? "Mic off — tap to listen" : "Tap to talk";
    promptStatus();
  },
});

function toggleMic() {
  if (listener.open) { listener.close(); return; }
  // the window restarts every time you speak, so it only closes after this long without speech
  const ms = settings.micMode === "always"
    ? (settings.sleepMin ? settings.sleepMin * 60e3 : 24 * 3600e3)
    : settings.listenWindow * 1000;
  listener.openWindow(ms);
}

// ---------- settings sheet

function setupSettings() {
  const dlg = $("settings");
  $("gear").onclick = () => { fillSettings(); dlg.showModal(); };
  const bind = (id, key, parse = v => v) => { $(id).onchange = e => { settings[key] = parse(e.target.type === "checkbox" ? e.target.checked : e.target.value); saveSettings(); }; };
  bind("listenWindow", "listenWindow", Number);
  bind("sleepMin", "sleepMin", Number);
  bind("delay", "delay", Number);
  bind("showEnglish", "showEnglish");
  bind("reviewMastered", "reviewMastered");
  $("reviewMastered").addEventListener("change", () => { rebuildQueue(); if (!S.solved) nextCard(); });
  $("showEnglish").addEventListener("change", () => { if (S.card && !S.solved) $("english").textContent = settings.showEnglish ? S.card.english : ""; });
  $("keepAwake").onchange = e => { settings.keepAwake = e.target.checked; saveSettings(); applyKeepAwake(); };
  document.querySelectorAll('input[name="micMode"]').forEach(r => r.onchange = () => {
    settings.micMode = r.value; saveSettings(); listener.close();
  });
  $("signIn").onclick = async () => {
    const email = $("email").value.trim(), password = $("password").value;
    if (!email || !password) return;
    settings.email = email;
    saveSettings();
    await secure.set("yoyo_password", password);
    $("password").value = "";
    sync.setCredentials({ email, password });
    S.syncState = "checking";
    showSyncStatus();
    S.roundRequested = false;
    sync.requestDue();
  };
  $("signOut").onclick = async () => { await secure.remove("yoyo_password"); sync.signOut(); showSyncStatus(); };
  $("syncNow").onclick = () => { sync.syncNow(true); sync.requestDue(); };
}

function fillSettings() {
  $("email").value = settings.email || "";
  $("listenWindow").value = String(settings.listenWindow);
  $("sleepMin").value = String(settings.sleepMin);
  $("delay").value = String(settings.delay);
  $("showEnglish").checked = settings.showEnglish;
  $("reviewMastered").checked = settings.reviewMastered;
  $("keepAwake").checked = settings.keepAwake;
  document.querySelector(`input[name="micMode"][value="${settings.micMode}"]`).checked = true;
  showSyncStatus();
}

function applyKeepAwake() {
  if (!native) return;
  (settings.keepAwake ? KeepAwake.keepAwake() : KeepAwake.allowSleep()).catch(() => {});
}

// ---------- start

async function loadDecks() {
  for (const [file, name] of DECKS) {
    const cards = await (await fetch(`decks/${file}`)).json();
    S.decks[name] = cards;
    for (const c of cards) {
      S.allCards[c.code] = c;
      if (c.mastered && !S.progress[c.code]) S.progress[c.code] = { level: MASTERED, ok: 0, fail: 0 };
    }
  }
  const sel = $("deck");
  sel.innerHTML = ["All decks", ...Object.keys(S.decks)].map(n => `<option>${n}</option>`).join("");
  if (!S.decks[settings.deck]) settings.deck = "All decks";
  sel.value = settings.deck;
  sel.onchange = () => { settings.deck = sel.value; saveSettings(); rebuildQueue(); nextCard(); };
}

async function main() {
  setupSettings();
  $("mic").onclick = toggleMic;
  $("card").onclick = () => {  // the whole card is a big mic button (long-press still selects text)
    if (!window.getSelection().toString()) toggleMic();
  };
  $("hear").onclick = speak;
  $("gotit").onclick = gotIt;
  $("skip").onclick = skip;
  $("pass").onclick = pass;
  $("sync").onclick = () => { if (sync.signedIn) { sync.syncNow(true); sync.requestDue(); } else { fillSettings(); $("settings").showModal(); } };
  window.addEventListener("resize", () => { if (S.hzArgs) fitHz(); });
  applyKeepAwake();
  showLoading("Loading cards…");
  await loadDecks();

  const password = settings.email ? await secure.get("yoyo_password") : null;
  if (settings.email && password) sync.credentials = { email: settings.email, password };
  if (sync.signedIn) {
    setTimeout(() => dueFallback("YoYo didn't answer"), DUE_WAIT_MS);
    nextCard();  // shows "Asking YoYo which cards are due…" until the list arrives
  } else {
    rebuildQueue();
    nextCard();
  }
  showSyncStatus();
  sync.start();
}

main();
