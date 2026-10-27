// Grading: does what the recognizer heard match the card? A port of the PC app's rules
// (app.py): exact characters, same sounds (tones judged separately), near-miss syllables,
// the answer inside a longer sentence, optional 儿 / bracketed parts, English words in cards.
import { pinyin } from "pinyin-pro";

const TONE_MARKS = {};
for (const [base, marks] of Object.entries({ a: "āáǎà", e: "ēéěè", i: "īíǐì", o: "ōóǒò", u: "ūúǔù", v: "ǖǘǚǜ" })) {
  [...marks].forEach((ch, t) => { TONE_MARKS[ch] = [base, t + 1]; });
}

export const isHanzi = ch => ch >= "一" && ch <= "鿿";
export const stripHanzi = text => [...text].filter(isHanzi).join("");

/** Toneless letters and tone sequence of a tone-marked pinyin string. */
export function normalizePinyin(text) {
  const s = text.normalize("NFC").toLowerCase().replaceAll("u:", "v").replaceAll("ü", "v");
  let letters = "";
  const tones = [];
  for (const ch of s) {
    if (TONE_MARKS[ch]) {
      letters += TONE_MARKS[ch][0];
      tones.push(TONE_MARKS[ch][1]);
    } else if (ch >= "a" && ch <= "z") {
      letters += ch;
    }
  }
  return [letters, tones];
}

const sameTones = (a, b) => a.length === b.length && a.every((t, i) => t === b[i]);

/** One tone-marked syllable per character (phrase-aware, like pypinyin). */
function syllables(chars) {
  if (!chars) return [];
  return pinyin(chars, { type: "array", toneType: "symbol", nonZh: "removed" });
}

/** Heard hanzi as pinyin, with erhua merged: 哪儿 -> "nǎr". */
export function hanziToPinyin(text) {
  const chars = [...text];
  const syls = syllables(text);
  chars.forEach((ch, i) => {
    if (ch === "儿" && i > 0 && i < syls.length) {
      syls[i - 1] += "r";
      syls[i] = "";
    }
  });
  return syls.filter(Boolean).join(" ");
}

/** Per heard character: [hanzi, letters, tones, display pinyin]. */
export function hanziUnits(text) {
  const chars = [...stripHanzi(text)];
  const syls = syllables(chars.join(""));
  return chars.map((ch, i) => {
    const [letters, tones] = normalizePinyin(syls[i] || "");
    return [ch, letters, tones, syls[i] || ""];
  });
}

/** Per card character: [simplified, traditional, letters, tones, display pinyin]. */
export function cardUnits(card) {
  const simp = [...stripHanzi(card.simplified)];
  let trad = [...stripHanzi(card.traditional || "")];
  if (trad.length !== simp.length) trad = simp;
  const firstAlt = card.pinyin.split("/")[0];
  const tokens = firstAlt.toLowerCase().split(/[^\p{L}\p{N}_]+/u).filter(Boolean);
  let syls;
  if (tokens.length === simp.length) {
    syls = tokens.map(t => [...normalizePinyin(t), t]);
  } else {  // joined syllables / erhua / extra latin: fall back to the dictionary
    syls = hanziUnits(simp.join("")).map(u => u.slice(1));
  }
  return simp.map((s, i) => [s, trad[i], ...(syls[i] || ["", [], ""])]);
}

/** Card character indices you may leave out: bracketed parts and erhua 儿 (not 儿子 ér zi). */
export function optionalUnits(card) {
  const text = card.simplified;
  const tokens = card.pinyin.normalize("NFC").toLowerCase().split(/[^\p{L}\p{N}_]+/u);
  const erSyllable = tokens.some(t => t && normalizePinyin(t)[0] === "er");
  const optional = new Set();
  let k = 0, depth = 0;
  for (const ch of text) {
    if (ch === "(" || ch === "（") depth++;
    else if (ch === ")" || ch === "）") depth = Math.max(0, depth - 1);
    else if (isHanzi(ch)) {
      if (depth || (ch === "儿" && k > 0 && !erSyllable)) optional.add(k);
      k++;
    }
  }
  return optional;
}

/** Syllables the recognizer confuses: an added/dropped final n (nǐ/nín), ia/iao (jiā/jiāo). */
export function nearSyllable(a, b) {
  for (const [x, y] of [[a, b], [b, a]]) {
    if (x + "n" === y || (x.endsWith("ia") && x + "o" === y)) return true;
  }
  return false;
}

const SANDHI = new Set(["一", "不"]);

/** [matches, toneOk] for one card unit vs one heard unit. */
export function unitMatch(unit, heard, strict) {
  if (heard[0] === unit[0] || heard[0] === unit[1]) return [true, true];
  if (heard[1] !== unit[2]) {
    // a near neighbour: accept, and don't judge the tone of the misheard word
    return nearSyllable(heard[1], unit[2]) ? [true, true] : [false, false];
  }
  // 一/不 change tone with context and cards write the changed tone; neutral tones can't
  // be judged either (the recognizer reports characters, whose dictionary tone isn't neutral)
  const toneOk = sameTones(heard[2], unit[3]) || SANDHI.has(unit[0]) || !unit[3].length || !heard[2].length;
  return [toneOk || !strict, toneOk];
}

/** The whole card as a contiguous run inside the utterance: toneOk of the best match, or null. */
export function findWithin(units, heard, strict) {
  const n = units.length;
  let best = null;
  for (let i = 0; i + n <= heard.length; i++) {
    const results = units.map((u, k) => unitMatch(u, heard[i + k], strict));
    if (results.every(r => r[0])) {
      if (results.every(r => r[1])) return true;
      best = false;
    }
  }
  return best;
}

/** Align an utterance to the card. matched: card indices said right; pairs: card -> heard index. */
export function alignUnits(units, heard, strict) {
  const n = units.length, m = heard.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  const ok = (i, j) => unitMatch(units[i], heard[j], strict)[0];
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = ok(i, j) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const anchors = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (ok(i, j) && dp[i][j] === dp[i + 1][j + 1] + 1) { anchors.push([i, j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  const pairs = new Map(anchors);
  // pair substitutions in the gaps between matches; the leading gap aligns to its end
  const bounds = [[-1, -1], ...anchors, [n, m]];
  for (let b = 0; b + 1 < bounds.length; b++) {
    const [ai, aj] = bounds[b], [bi, bj] = bounds[b + 1];
    let cg = [], hg = [];
    for (let x = ai + 1; x < bi; x++) cg.push(x);
    for (let y = aj + 1; y < bj; y++) hg.push(y);
    if (ai === -1) { cg = cg.reverse(); hg = hg.reverse(); }
    for (let z = 0; z < Math.min(cg.length, hg.length); z++) pairs.set(cg[z], hg[z]);
  }
  return { matched: new Set(anchors.map(a => a[0])), pairs };
}

/** Whole-utterance check: [correct, heardPinyin, toneOk]. */
export function checkWhole(card, heard) {
  const heardHanzi = stripHanzi(heard);
  const heardPy = hanziToPinyin(heardHanzi);
  if (heardHanzi && (heardHanzi === stripHanzi(card.simplified) || heardHanzi === stripHanzi(card.traditional || ""))) {
    return [true, heardPy, true];
  }
  const [gotLetters, gotTones] = normalizePinyin(heardPy);
  if (!gotLetters) return [false, heardPy, false];
  let best = [false, heardPy, false];
  for (const alt of card.pinyin.split("/")) {  // e.g. "wèi / wéi"
    const [wantLetters, wantTones] = normalizePinyin(alt);
    if (gotLetters !== wantLetters) continue;
    if (sameTones(gotTones, wantTones)) return [true, heardPy, true];
    best = [true, heardPy, false];
  }
  return best;
}

const LATIN_GAP_MAX = 4;  // hanzi an English word in a card may come out as

/** All characters said in order, with extra hanzi only where the card has an English word. */
function onlyLatinGaps(card, pairs) {
  const gaps = new Set();
  let k = 0;
  for (const ch of card.simplified) {
    if (isHanzi(ch)) k++;
    else if (/[A-Za-z]/.test(ch)) gaps.add(k);
  }
  const at = [...Array(pairs.size).keys()].map(i => pairs.get(i));
  for (let i = 1; i < at.length; i++) {
    const extra = at[i] - at[i - 1] - 1;
    if (extra && (!gaps.has(i) || extra > LATIN_GAP_MAX)) return false;
  }
  return gaps.size > 0;
}

/**
 * Grade one utterance against a card.
 * Returns { ok, toneOk, heardPy, matched:Set, ruby:Map(card index -> heard pinyin), toneOff:Set }.
 */
export function check(card, heard) {
  let [ok, heardPy, toneOk] = checkWhole(card, heard);
  const units = cardUnits(card);
  const heardUnits = hanziUnits(heard);
  const strict = false;  // tone slips are reported separately (toneOff), not as misses
  if (!ok) {
    let within = units.length ? findWithin(units, heardUnits, strict) : null;
    const optional = optionalUnits(card);
    if (within === null && optional.size) {  // try again without the optional 儿 / bracketed part
      const required = units.filter((_, i) => !optional.has(i));
      within = required.length ? findWithin(required, heardUnits, strict) : null;
    }
    if (within !== null) { ok = true; toneOk = within; }
  }
  let { matched, pairs } = alignUnits(units, heardUnits, strict && !ok);
  if (!ok && units.length && matched.size === units.length && onlyLatinGaps(card, pairs)) ok = true;
  if (ok) matched = new Set(units.keys());
  const toneOff = new Set();
  for (const [i, j] of pairs) {
    if (matched.has(i) && !unitMatch(units[i], heardUnits[j], false)[1]) toneOff.add(i);
  }
  const ruby = new Map([...pairs].map(([i, j]) => [i, heardUnits[j][3]]));
  if (ok && pairs.size === units.length) toneOk = true;  // per-character tones are the better judge
  return { ok, toneOk: toneOk && toneOff.size === 0, heardPy, matched, ruby, toneOff };
}

/** Best of the recognizer's alternatives: right with tones > right > most characters matched. */
export function checkBest(card, alternatives) {
  let best = null;
  const score = r => (r.ok ? (r.toneOk ? 3 : 2) : 0) * 1000 + r.matched.size;
  for (const text of alternatives) {
    if (!text) continue;
    const r = { ...check(card, text), text };
    if (!best || score(r) > score(best)) best = r;
  }
  return best;
}

/** Per character: [pinyin you said, verdict] for the 🎙 row. */
export function saidRow(card, result) {
  const out = new Map();
  cardUnits(card).forEach((_, i) => {
    if (result.toneOff.has(i)) out.set(i, [result.ruby.get(i) || "—", "tone"]);
    else if (result.ruby.has(i)) out.set(i, [result.ruby.get(i), result.matched.has(i) ? "ok" : "bad"]);
    else out.set(i, ["—", result.ok ? "skip" : "bad"]);  // nothing heard: fine if optional and passed
  });
  return out;
}

const TONE_NAMES = { 1: ["1st", "high"], 2: ["2nd", "rising"], 3: ["3rd", "dipping"], 4: ["4th", "falling"] };
export function toneName(syllable, long = true) {
  const tones = normalizePinyin(syllable)[1];
  const [num, shape] = tones.length && TONE_NAMES[tones[0]] ? TONE_NAMES[tones[0]] : ["neutral", ""];
  return long && shape ? `${num}, ${shape}` : num;
}

/** Saying "pass" (or 跳过) skips the card. Google's Chinese recognizer writes English words in Latin. */
export function isPass(alternatives) {
  return alternatives.some(t => /^\s*(pass|passed|跳过)\s*[.。!！]?\s*$/i.test(t || ""));
}

/** A card's correct pinyin per character, for the 🎯 row. */
export const answerRuby = card => new Map(cardUnits(card).map((u, i) => [i, u[4]]));
