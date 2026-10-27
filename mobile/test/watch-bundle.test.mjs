// The watch's grading bundle must run as a plain script and agree with the phone's grading.
import fs from "node:fs";
import vm from "node:vm";
import { checkBest } from "../src/grading.js";

const ctx = vm.createContext({});  // nothing but the JS language: no Node, no browser
vm.runInContext(fs.readFileSync("../wear/app/src/main/assets/grading.js", "utf8"), ctx);
const cases = JSON.parse(fs.readFileSync("test/cases.json", "utf8"));
let same = 0, total = 0;
for (const c of cases) {
  const card = c.card, heard = c.heard ?? c.text ?? c[1];
  if (!card || heard === undefined) continue;
  const mine = checkBest(card, [heard]);
  const watch = JSON.parse(ctx.SS.grade(JSON.stringify(card), JSON.stringify([heard])));
  total++;
  if ((mine?.ok ?? null) === (watch?.ok ?? null) && (mine?.toneOk ?? null) === (watch?.toneOk ?? null)) same++;
}
const card = { code: "x", simplified: "我是沈阳人。", traditional: "我是瀋陽人。", pinyin: "wǒ shì shěn yáng rén", english: "" };
const r = JSON.parse(ctx.SS.grade(JSON.stringify(card), JSON.stringify(["我是陈阳人"])));
console.log(`watch bundle agrees on ${same}/${total} cases | 沈阳 mishear ok: ${r.ok} | pass: ${ctx.SS.isPass('["pass"]')} | answer:`, ctx.SS.answer(JSON.stringify(card)).slice(0, 80));
