// Compares the JS grading port with the PC app's verdicts (test/cases.json from make_cases.py).
import { readFileSync } from "node:fs";
import { check, isPass } from "../src/grading.js";

const cases = JSON.parse(readFileSync(new URL("./cases.json", import.meta.url), "utf-8"));
let pass = 0, rubyDiff = 0;
const fails = [];
for (const c of cases) {
  const r = check(c.card, c.heard);
  const got = { ok: r.ok, toneOk: r.toneOk, matched: [...r.matched].sort((a, b) => a - b), toneOff: [...r.toneOff].sort((a, b) => a - b) };
  const w = c.want;
  const same = got.ok === w.ok && got.toneOk === w.toneOk
    && JSON.stringify(got.matched) === JSON.stringify(w.matched) && JSON.stringify(got.toneOff) === JSON.stringify(w.toneOff);
  if (same) pass++; else fails.push({ card: c.card.simplified, heard: c.heard, manual: c.manual, want: w, got });
  const ruby = Object.fromEntries([...r.ruby].map(([k, v]) => [String(k), v]));
  if (JSON.stringify(ruby) !== JSON.stringify(w.ruby)) rubyDiff++;
}
console.log(`verdicts match: ${pass}/${cases.length} | heard-pinyin display differs on ${rubyDiff}`);
for (const f of fails.slice(0, 12)) console.log("MISMATCH", JSON.stringify(f));
console.log("pass words:", isPass(["pass"]), isPass(["Pass."]), isPass(["跳过"]), "| not pass:", isPass(["怕"]), isPass(["passport"]));
process.exit(fails.filter(f => f.manual).length ? 1 : 0);
