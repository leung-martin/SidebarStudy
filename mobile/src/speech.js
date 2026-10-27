// Listening with Android's speech recognizer (Google), in Chinese.
//
// Android's recognizer handles one phrase per session, so a "listening window" keeps starting
// new sessions until the window closes. Tap-to-talk: a tap opens the window for a minute, and
// every time you speak it resets to the full minute. Always-on: the window lasts until you've
// been silent for the chosen time.
//
// The plugin is patched (patches/ via patch-package) so that a session ending in silence also
// says "stopped" and late callbacks from replaced sessions are ignored. It still says "stopped"
// BEFORE delivering a phrase's final guesses, so a phrase is graded after a short wait for them.
// A slow poll is kept as a safety net in case a session dies without any event.
import { SpeechRecognition } from "@capacitor-community/speech-recognition";

const LANG = "zh-CN";
const POLL_MS = 400;          // how often to check whether the session is still running
const FINAL_WAIT_MS = 700;    // after a phrase ends, wait this long for the final guesses
const RESTART_MS = 150;
const COOLDOWN_MS = 250;      // a short breather between sessions for Google's recognizer
const INSTANT_FAIL_MS = 400;  // a session that errors within this long never really started
const RETRY_AFTER_FAIL_MS = 900;
const DEAD_SESSION_MS = 3000; // no event and not listening for this long: the session died

export class Listener {
  /** handlers: {partial(matches), final(matches), state({listening}), error(msg)} */
  constructor(handlers) {
    this.h = handlers;
    this.windowMs = 60e3;     // the window length (reset to this whenever you speak)
    this.deadline = 0;        // ms timestamp the window closes (0 = closed)
    this.session = 0;         // id of the running recognizer session (0 = none)
    this.paused = false;      // e.g. while the app is speaking
    this.latest = [];         // newest guesses in the current session
    this.ending = null;       // timer waiting for a finished phrase's final guesses
    this.lastEvent = 0;       // last time the recognizer told us anything
    this.notBefore = 0;       // don't start a new session before this time (recognizer cooldown)
    this.startTimer = null;
    this.trace = [];          // recent events, readable over WebView debugging (window.__listener)
    window.__listener = this;
    this.poll = null;
    this.ready = this.init();
  }

  async init() {
    try {
      const { available } = await SpeechRecognition.available();
      if (!available) { this.h.error("Speech recognition isn't available on this phone."); return false; }
      let perm = await SpeechRecognition.checkPermissions();
      if (perm.speechRecognition !== "granted") perm = await SpeechRecognition.requestPermissions();
      if (perm.speechRecognition !== "granted") { this.h.error("Microphone permission was denied."); return false; }
      await SpeechRecognition.addListener("partialResults", d => this.onResults(d.matches || []));
      await SpeechRecognition.addListener("listeningState", d => {
        this.lastEvent = Date.now();
        this.log(`event ${d.status}${d.error ? " (" + d.error + ")" : ""}`);
        if (d.status !== "stopped") return;
        if (d.error) this.sessionFailed();
        else this.phraseEnded();
      });
      return true;
    } catch (e) {
      this.h.error(`Speech setup failed: ${e.message || e}`);
      return false;
    }
  }

  get open() { return this.deadline > Date.now(); }

  log(what) {
    this.trace.push(`${new Date().toISOString().slice(11, 23)} ${what}`);
    if (this.trace.length > 200) this.trace.shift();
  }

  /** Open the listening window; it stays open `ms` past the last time you spoke. */
  async openWindow(ms) {
    if (!(await this.ready)) return;
    this.windowMs = ms;
    this.deadline = Date.now() + ms;
    this.startPolling();
    this.h.state({ listening: true });
    this.run("window opened");
  }

  close() {
    this.deadline = 0;
    this.stopSession();
    this.h.state({ listening: false });
  }

  /** You spoke: the window starts over at its full length. */
  heardSpeech() {
    if (this.open) this.deadline = Date.now() + this.windowMs;
  }

  pause() {
    this.paused = true;
    this.stopSession();
  }

  resume() {
    this.paused = false;
    this.run("resume");
  }

  /** Throw away whatever was heard so far (a new card is showing). */
  restart() {
    this.stopSession();
    setTimeout(() => this.run("restart"), RESTART_MS);
  }

  // ---------- internals

  async run(why = "") {
    if (this.session || this.paused || !this.open) return;
    const wait = this.notBefore - Date.now();
    if (wait > 0) {  // the recognizer needs a moment between sessions
      clearTimeout(this.startTimer);
      this.startTimer = setTimeout(() => this.run(why), wait);
      return;
    }
    const id = this.session = Date.now();
    this.latest = [];
    this.log(`start ${why}`);
    try {
      await SpeechRecognition.start({ language: LANG, maxResults: 5, partialResults: true, popup: false });
    } catch (e) {
      this.log(`start failed: ${e?.message || e}`);
      if (this.session === id) this.session = 0;  // busy or a stale error: the poll retries
    }
  }

  stopSession() {
    clearTimeout(this.ending);
    this.ending = null;
    this.latest = [];
    if (!this.session) return;
    this.session = 0;
    this.notBefore = Date.now() + COOLDOWN_MS;
    this.log("stop");
    SpeechRecognition.stop().catch(() => { /* already stopped */ });
  }

  onResults(matches) {
    this.lastEvent = Date.now();
    this.log(`results x${matches.length}${this.ending ? " (final)" : ""}`);
    if (!matches.length || !this.session) return;
    this.latest = matches;
    this.heardSpeech();
    if (this.ending) {  // the phrase's final guesses: grade them now instead of waiting it out
      clearTimeout(this.ending);
      this.ending = null;
      this.finishSession("phrase done", COOLDOWN_MS);
      return;
    }
    this.h.partial(matches);
  }

  /** A phrase ended: give the recognizer a moment to deliver its final guesses, then grade. */
  phraseEnded() {
    if (!this.session || this.ending) return;
    const id = this.session;
    this.ending = setTimeout(() => {
      this.ending = null;
      if (this.session === id) this.finishSession("phrase done", COOLDOWN_MS - FINAL_WAIT_MS);
    }, FINAL_WAIT_MS);
  }

  /** The session ended with an error (silence, no match, busy): grade anything heard, go again. */
  sessionFailed() {
    if (!this.session) return;  // the plugin often reports the same error twice
    clearTimeout(this.ending);
    this.ending = null;
    const instant = Date.now() - this.session < INSTANT_FAIL_MS && !this.latest.length;
    this.finishSession(instant ? "failed at start" : "session ended", instant ? RETRY_AFTER_FAIL_MS : COOLDOWN_MS);
  }

  finishSession(why, cooldownMs) {
    const heard = this.latest;
    this.session = 0;
    this.latest = [];
    this.notBefore = Date.now() + Math.max(0, cooldownMs);
    this.log(`${why}, graded ${heard.length} guesses`);
    if (heard.length) this.h.final(heard);
    setTimeout(() => this.run("next phrase"), RESTART_MS);
  }

  startPolling() {
    if (this.poll) return;
    this.poll = setInterval(async () => {
      if (!this.open) {  // window over
        clearInterval(this.poll);
        this.poll = null;
        this.close();
        return;
      }
      if (this.paused || this.ending) return;
      if (!this.session) { this.run("poll: no session"); return; }
      // safety net: a session that has gone quiet and isn't listening, with no event for a
      // while, died without telling us — start a new one
      const quiet = Date.now() - Math.max(this.session, this.lastEvent) > DEAD_SESSION_MS;
      if (!quiet) return;
      let listening = true;
      try { listening = (await SpeechRecognition.isListening()).listening; } catch { /* keep going */ }
      if (!listening && this.session && !this.ending) {
        this.log("poll: session died silently");
        if (this.latest.length) this.phraseEnded();
        else { this.session = 0; this.run("after dead session"); }
      }
    }, POLL_MS);
  }
}
