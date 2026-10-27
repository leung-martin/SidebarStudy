// The phone app's grading, packaged for the Wear OS app (wear/), which runs it in QuickJS.
// Built by `npm run build:watch` into wear/app/src/main/assets/grading.js. Everything goes in
// and out as JSON strings so the Kotlin side only ever calls SS.<fn>(...) and parses a string.
import { checkBest, saidRow, isPass, isHanzi, stripHanzi, toneName, answerRuby } from "./grading.js";

/** The card's text split for drawing: [{ch, k}] where k is the hanzi index (-1 for other text). */
function parts(card) {
  let k = 0;
  return (card.simplified.match(/[A-Za-z]+|./gsu) || []).map(ch => ({ ch, k: ch.length === 1 && isHanzi(ch) ? k++ : -1 }));
}

globalThis.SS = {
  /** Drawing parts plus the answer pinyin per hanzi. */
  answer(cardJson) {
    const card = JSON.parse(cardJson);
    return JSON.stringify({ parts: parts(card), ruby: Object.fromEntries(answerRuby(card)) });
  },

  /** Grade what was heard: null if nothing usable, else the verdict with per-character detail. */
  grade(cardJson, altsJson) {
    const card = JSON.parse(cardJson);
    const r = checkBest(card, JSON.parse(altsJson));
    if (!r) return "null";
    const ans = answerRuby(card);
    const chars = [...stripHanzi(card.simplified)];
    const slips = [...r.toneOff].sort((a, b) => a - b).map(i =>
      `${chars[i]} ${ans.get(i)} — ${toneName(ans.get(i))} tone (you said ${toneName(r.ruby.get(i) || "", false)})`);
    return JSON.stringify({
      ok: r.ok, toneOk: r.toneOk, text: r.text, heardPy: r.heardPy, shown: stripHanzi(r.text) || r.text,
      matched: [...r.matched], toneOff: [...r.toneOff], ruby: Object.fromEntries(r.ruby),
      said: Object.fromEntries(saidRow(card, r)), slips,
    });
  },

  isPass(altsJson) { return isPass(JSON.parse(altsJson)); },
};
