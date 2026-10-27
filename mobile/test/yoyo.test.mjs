// 1) fake YoYo: answers sent, guard stops on a drop, expired session re-signs-in.
// 2) real YoYo, READ-ONLY (session token from the PC app): one level, the due list, me.
import { readFileSync } from "node:fs";
import { YoyoSync, nextLevel } from "../src/yoyo.js";

const memStore = () => { const m = {}; return { get: k => m[k], set: (k, v) => { m[k] = v; } }; };
const events = log => ({ status: s => log.push(["status", s]), levels: (l, t, full) => log.push(["levels", l, full]), due: d => log.push(["due", d]) });

function fakeServer({ evil = false, expireOnce = false } = {}) {
  const levels = { A: 2, B: -1, C: 5, D: 0, E: 4 };
  const state = { submits: [], logins: 0, expired: expireOnce };
  const page = (codes, masteryType) => {
    const list = codes.filter(c => masteryType === "all" || (levels[c] >= 5) === (masteryType === "mastered"));
    list.sort(() => Math.random() - 0.5);   // unstable paging, like YoYo
    return { totalFlashcards: list.length, flashcards: list.slice(0, 50).map(c => ({ _id: "id" + c, code: c, levelId: "L1", masteryLevel: levels[c] })) };
  };
  const http = async (method, url, headers, body) => {
    const path = url.replace("https://yoyochinese.com/api/v1", "");
    if (path === "/auth/login") {  // like YoYo: a plain token in the body, the real session in Set-Cookie
      state.logins++;
      return body.password === "pw"
        ? { status: 200, data: { token: "plain" }, headers: { "Set-Cookie": `token=tok${state.logins}; Path=/; HttpOnly` } }
        : { status: 401, data: {} };
    }
    if (state.expired) { state.expired = false; return { status: 401, data: {} }; }
    if (!headers.Cookie?.startsWith("token=tok")) return { status: 401, data: {} };
    if (path === "/page/flashcards-mastery-manage_page") return { status: 200, data: { pageData: { filterOptions: {
      courseOptions: [], levelOptions: [{ value: "L1", courseId: "C1" }], unitOptions: [{ value: "U1", levelId: "L1" }],
      lessonOptions: [{ value: "l1", unitId: "U1" }, { value: "l2", unitId: "U1" }] } } } };
    if (path === "/flashcards/manage/cards") {
      const codes = body.filters.lessonId === "l1" ? ["A", "B"] : ["C", "D", "E"];
      return { status: 200, data: page(codes, body.filters.masteryType) };
    }
    if (path === "/results/flashcard-mastery") {
      state.submits.push(body.responses.map(r => [r.flashcardId.slice(2), r.answer]));
      for (const r of body.responses) levels[r.flashcardId.slice(2)] = nextLevel(levels[r.flashcardId.slice(2)], r.answer);
      if (evil) levels.E = 0;
      return { status: 200, data: {} };
    }
    if (path === "/me/provider") return { status: 200, data: { meData: { isValid: true, masteredFlashcards: 1 } } };
    return { status: 404, data: {} };
  };
  return { http, levels, state };
}

async function runFake(opts) {
  const srv = fakeServer(opts), log = [];
  const s = new YoyoSync(srv.http, memStore(), events(log));
  s.credentials = { email: "me@x", password: "pw" };
  s.wantDue = false;
  await s.pull();
  s.record("A", "good"); s.record("C", "hard"); s.record("B", "bogus");
  clearTimeout(s.timer);
  await s.push();
  s.record("D", "easy"); clearTimeout(s.timer);
  await s.push();
  return { srv, s, log };
}

let r = await runFake({});
console.log("fake normal:   sent", JSON.stringify(r.srv.state.submits), "| yoyo", JSON.stringify(r.srv.levels), "| blocked:", JSON.stringify(r.s.blocked));
r = await runFake({ evil: true });
console.log("fake bad sync: sent", JSON.stringify(r.srv.state.submits), "| blocked:", JSON.stringify(r.s.blocked), "| still pending:", r.s.cache.pending.map(p => p.code));
r = await runFake({ expireOnce: true });
console.log("fake expired session: logins", r.srv.state.logins, "| sends still worked:", r.srv.state.submits.length === 2);

// ---- real YoYo, read-only
const ck = JSON.parse(readFileSync(new URL("../../yoyo_auth.json", import.meta.url), "utf-8")).cookie;
const token = ck.split(";").map(p => p.trim()).find(p => p.startsWith("token=")).slice(6);
const http = async (method, url, headers, body) => {
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: await res.json().catch(() => null), headers: Object.fromEntries(res.headers) };
};
const store = memStore(); store.set("yoyo_token", token);
const s = new YoyoSync(http, store, events([]));
const me = await s.me();
let t = Date.now();
const lvl = await s.fetch("5f9c5382c32d410f1447befc");
console.log(`real: signed in ${me.isValid}, mastered ${me.masteredFlashcards} | ICC level 2: ${lvl.length} cards in ${Date.now() - t} ms`);
t = Date.now();
const due = await s.due();
console.log(`real: due list ${due.length} cards in ${Date.now() - t} ms | mastered unchanged: ${(await s.me()).masteredFlashcards === me.masteredFlashcards}`);
