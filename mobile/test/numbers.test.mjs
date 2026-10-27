// Google writes spoken numbers as digits: those must still grade as the card's Chinese numerals.
import fs from "node:fs";
import { checkBest, numberVariants } from "../src/grading.js";

let fail = 0;
const expect = (label, got, want) => { if (got !== want) { fail++; console.log(`✗ ${label}: got ${got}, want ${want}`); } };

// the readings themselves
const has = (text, reading) => numberVariants(text).includes(reading);
for (const [text, reading] of [
  ["2", "二"], ["2", "两"], ["10", "十"], ["12", "十二"], ["20", "二十"], ["99", "九十九"], ["100", "一百"],
  ["110", "一百一十"], ["105", "一百零五"], ["200", "两百"], ["200", "二百"], ["1005", "一千零五"],
  ["2000", "两千"], ["10000", "一万"], ["20500", "两万零五百"], ["100000", "十万"], ["2026年", "二零二六年"],
  ["2026年", "二〇二六年"], ["3.5", "三点五"], ["50%", "百分之五十"], ["3:30", "三点三十分"], ["1,000", "一千"],
  ["我25岁", "我二十五岁"], ["１２", "十二"], ["250", "二百五"], ["2500", "两千五"], ["15000", "一万五"],
  ["380", "三百八十"],
]) if (!has(text, reading)) { fail++; console.log(`✗ ${text} should read as ${reading}: ${numberVariants(text).join(" | ")}`); }

// real cards with numbers, heard as Google would write them
const NUM = /[零〇一二两三四五六七八九十百千万亿]+/g;
const toDigits = s => {  // simple numbers only: the cases Google turns into digits
  const map = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (/^[零〇一二三四五六七八九]{2,}$/.test(s)) return [...s].map(c => map[c]).join("");
  if (/[一二三四五六七八九]{2}十|万万/.test(s)) return null;  // ranges (四五十) and 万万 aren't numbers Google writes
  let total = 0, sec = 0, num = 0, last = 1;
  for (const c of s) {
    if (c in map) num = map[c];
    else if (c === "十") { sec += (num || 1) * 10; num = 0; last = 10; }
    else if (c === "百") { sec += num * 100; num = 0; last = 100; }
    else if (c === "千") { sec += num * 1000; num = 0; last = 1000; }
    else if (c === "万") { total += (sec + num) * 1e4; sec = 0; num = 0; last = 1e4; }
    else return null;
  }
  // 二百五 = 250: a digit right after 百/千/万 counts in the next place down
  const tail = num && /[百千万]$/.test(s.slice(0, -1)) ? num * last / 10 : num;
  return String(total + sec + tail);
};
let tried = 0, wrong = 0;
for (const file of fs.readdirSync("public/decks").filter(f => f.endsWith(".json"))) {
  for (const card of JSON.parse(fs.readFileSync(`public/decks/${file}`, "utf8"))) {
    const s = card.simplified;
    // only numbers Google would write as digits: 2+ characters, or a lone digit next to a measure/time word
    const nums = (s.match(NUM) || []).filter(n => n.length > 1 && !/^(一|一一|万一|一万|十分|一千|千万)$/.test(n));
    if (!nums.length) continue;
    let heard = s, ok = true;
    for (const n of nums) { const d = toDigits(n); if (d === null) { ok = false; break; } heard = heard.replace(n, d); }
    if (!ok || heard === s) continue;
    tried++;
    const r = checkBest(card, [heard]);
    if (!(r?.ok)) { wrong++; if (wrong <= 15) console.log(`✗ ${s}  heard "${heard}"  -> ${r?.text}`); }
  }
}
console.log(`${tried} number cards heard as digits: ${tried - wrong} graded right, ${wrong} wrong`);
console.log(fail ? `${fail} reading check(s) failed` : "all readings ok");
