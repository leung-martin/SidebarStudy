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
// ---------- numbers: Google writes what you say as digits ("2", "25", "2026年", "50%"), so each
// number is tried the ways it could have been said: 二十五, 两百 or 二百, 二零二六 / 二〇二六 …

const ZH_DIGITS = "零一二三四五六七八九";
const MAX_NUMBER_VARIANTS = 32;

function sectionReading(n) {  // 1..9999: 一千零五, 三百二十
  const units = ["", "十", "百", "千"], ds = [...String(n)].map(Number);
  let out = "", zero = false;
  ds.forEach((d, i) => {
    if (!d) { zero = out !== ""; return; }  // a gap reads 零 once, only before another digit
    if (zero) { out += "零"; zero = false; }
    out += ZH_DIGITS[d] + units[ds.length - 1 - i];
  });
  return out;
}

/** 0 -> 零, 12 -> 十二, 110 -> 一百一十, 20500 -> 二万零五百 (the 两 forms are added by intReadings). */
function intReading(n) {
  if (!n) return "零";
  const yi = Math.floor(n / 1e8), wan = Math.floor(n / 1e4) % 1e4, rest = n % 1e4;
  let out = yi ? intReading(yi) + "亿" : "";
  if (wan) out += (out && wan < 1000 ? "零" : "") + sectionReading(wan) + "万";
  if (rest) out += (out && rest < 1000 ? "零" : "") + sectionReading(rest);
  return out.startsWith("一十") ? out.slice(1) : out;  // 十二, not 一十二
}

const digitsReading = (s, zero = "零") => [...s].map(d => d === "0" ? zero : ZH_DIGITS[d]).join("");

/** The ways a run of digits can be said: as a number (with 二 or 两), or digit by digit (years,
 *  phone numbers, room numbers). */
function intReadings(s) {
  const n = Number(s), out = new Set();
  if (s.length <= 12 && !(s.length > 1 && s[0] === "0")) {
    const r = intReading(n);
    out.add(r);
    if (/^[一二三四五六七八九][百千万][一二三四五六七八九][十百千]$/.test(r)) out.add(r.slice(0, -1));  // 二百五, 一万五
    if (n === 2) out.add("两");
    for (const v of [...out]) if (/^二[百千万亿]/.test(v)) out.add("两" + v.slice(1));  // 两百, 两千五
  }
  if (s.length > 1) { out.add(digitsReading(s)); out.add(digitsReading(s, "〇")); }
  return [...out];
}

function numberReadings(token) {
  const pct = token.endsWith("%");
  const t = pct ? token.slice(0, -1) : token;
  let readings;
  const m = t.match(/^(\d+)([.:])(\d+)$/);
  if (m && m[2] === ".") {  // 3.5 -> 三点五
    readings = intReadings(m[1]).map(r => r + "点" + digitsReading(m[3]));
  } else if (m) {           // 3:30 -> 三点三十(分)
    readings = intReadings(m[1]).flatMap(h => intReadings(m[3]).flatMap(mm => [`${h}点${mm}`, `${h}点${mm}分`]));
  } else {
    readings = intReadings(t);
  }
  return pct ? readings.map(r => "百分之" + r) : readings;
}

/** heard text with its digits written out every plausible way (the text itself first). */
export function numberVariants(text) {
  const norm = text.replace(/[０-９]/g, d => String.fromCharCode(d.charCodeAt(0) - 0xfee0))
    .replace(/(\d),(?=\d{3})/g, "$1");  // 1,000 -> 1000
  const parts = norm.split(/(\d+(?:[.:]\d+)?%?)/);
  if (parts.length === 1) return [text];
  let variants = [""];
  parts.forEach((part, i) => {
    const options = i % 2 ? numberReadings(part) : [part];
    variants = variants.flatMap(v => options.map(o => v + o)).slice(0, MAX_NUMBER_VARIANTS);
  });
  return [text, ...variants];
}

// ---------- words Google keeps mishearing: what it writes -> what you said. Add to this when a
// word you say right is always marked wrong (each entry is also matched in letters, "Shenyang").
export const HEARD_AS = {
  沈阳: ["陈阳", "沉阳", "晨阳", "神阳", "深阳", "审阳", "陈杨", "沈杨", "shenyang", "shen yang"],
};

/** heard text with any known mishears put back to the word you said (the text itself first). */
export function mishearVariants(text) {
  let fixed = text;
  for (const [word, heard] of Object.entries(HEARD_AS)) {
    for (const h of heard) fixed = fixed.replace(new RegExp(h.replace(/ /g, "\\s*"), "gi"), word);
  }
  return fixed === text ? [text] : [text, fixed];
}

export function checkBest(card, alternatives) {
  let best = null;
  const score = r => (r.ok ? (r.toneOk ? 3 : 2) : 0) * 1000 + r.matched.size;
  for (const text of alternatives.flatMap(a => a ? mishearVariants(a).flatMap(numberVariants) : [])) {
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
