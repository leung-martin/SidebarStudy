package com.sidebarstudy.wear

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

// Two-way sync with yoyochinese.com — a port of the phone app's yoyo.js (and the PC's yoyo_sync.py).
//
// YoYo has no public API; this uses the private endpoints its website uses, signed in with your
// YoYo email + password (the session is renewed automatically when it expires). Levels follow
// YoYo: -1 new, 0-4 learning, 5 mastered. YoYo is the source of truth: the watch only sends
// per-card answers (Good +2 / Not Bad +1 / Forgot back to 0), and every send is checked by reading
// the course level before and after — if a card you did NOT answer lost level, sending stops for good.

private const val API = "https://yoyochinese.com/api/v1"
private const val PAGE_SIZE = 50          // the server caps pages at 50 cards
private const val WORKERS = 8
private const val UNION_ROUNDS = 12       // re-reads of an oversized group (paging order is unstable)
private const val FULL_PULL_EVERY = 6 * 3600_000L
private const val CHECK_EVERY = 5 * 60_000L
private const val PUSH_DEBOUNCE = 3_000L

const val MASTERED = 5
val LEVEL_DELTA = mapOf("hard" to -5, "good" to 1, "easy" to 2)
fun nextLevel(level: Int, answer: String) = (maxOf(level, 0) + LEVEL_DELTA.getValue(answer)).coerceIn(0, MASTERED)

class AuthError(message: String) : Exception(message)

class Resp(val status: Int, val data: JSONObject?, val setCookie: String)

/** Where the sync keeps its session and cache (the app's Store; a map in tests). */
interface SyncStorage {
    var token: String
    fun read(name: String): JSONObject?
    fun write(name: String, o: JSONObject)
}

/** POST path with a JSON body (and the session, if any) to YoYo. */
typealias Transport = (path: String, body: JSONObject, session: String?) -> Resp

class YoyoSync(
    private val store: SyncStorage,
    private val scope: CoroutineScope,
    private val events: Events,
    private val transport: Transport = ::httpPost,
) {
    interface Events {
        fun status(state: String)
        fun levels(levels: Map<String, Int>, startedAt: Long, full: Boolean)
        fun due(codes: List<String>, fetchedAt: Long)
    }

    private class CardRef(val id: String, val levelId: String)
    private class Pending(val code: String, val answer: String, val t: Long)

    private val lock = Any()
    private var token: String = store.token
    private val cards = HashMap<String, CardRef>()     // code -> YoYo ids
    private val pending = ArrayList<Pending>()          // answers not yet sent
    private var fullPull = 0L
    var blocked = ""; private set                       // set if a send ever lowered an unanswered card
    private var lessons = listOf<JSONObject>()
    private val known = HashMap<String, Int>()          // code -> YoYo level as of the last pull
    private var wantFull = true
    private var wantDue = true
    private val kick = Channel<Long>(Channel.CONFLATED)
    var credentials: Pair<String, String>? = null       // (email, password), from Store

    init {
        store.read("yoyo_cache.json")?.let { c ->
            c.optJSONObject("cards")?.let { o -> o.keys().forEach { k -> o.getJSONObject(k).let { cards[k] = CardRef(it.getString("id"), it.getString("levelId")) } } }
            c.optJSONArray("pending")?.let { a -> for (i in 0 until a.length()) a.getJSONObject(i).let { pending += Pending(it.getString("code"), it.getString("answer"), it.getLong("t")) } }
            fullPull = c.optLong("fullPull")
            blocked = c.optString("pushBlocked")
        }
    }

    // ---------- called by the app

    val signedIn get() = token.isNotEmpty() || credentials != null
    val pendingCount get() = synchronized(lock) { pending.size }
    fun levelIdOf(code: String) = synchronized(lock) { cards[code]?.levelId }

    fun signIn(creds: Pair<String, String>) {
        credentials = creds
        setToken("")
        syncNow(full = true)
    }

    fun signOut() {
        credentials = null
        setToken("")
        events.status("not signed in")
    }

    fun record(code: String, answer: String) {
        if (answer !in LEVEL_DELTA) return
        synchronized(lock) { pending += Pending(code, answer, System.currentTimeMillis()) }
        saveCache()
        kick.trySend(PUSH_DEBOUNCE)
    }

    fun syncNow(full: Boolean = false) { wantFull = wantFull || full; kick.trySend(0) }
    fun requestDue() { wantDue = true; kick.trySend(PUSH_DEBOUNCE) }

    fun start() {
        kick.trySend(0)  // first cycle right away
        scope.launch(Dispatchers.IO) {
            while (true) {
                val wait = withTimeoutOrNull(CHECK_EVERY) { kick.receive() }
                if (wait != null && wait > 0) delay(wait)
                kick.tryReceive()  // anything that came in during the wait is covered by this cycle
                cycle(check = wait == null)
            }
        }
    }

    // ---------- worker

    private fun setToken(t: String) { token = t; store.token = t }

    private fun saveCache() {
        val o = synchronized(lock) {
            JSONObject()
                .put("cards", JSONObject().also { j -> cards.forEach { (k, v) -> j.put(k, JSONObject().put("id", v.id).put("levelId", v.levelId)) } })
                .put("pending", JSONArray().also { a -> pending.forEach { a.put(JSONObject().put("code", it.code).put("answer", it.answer).put("t", it.t)) } })
                .put("fullPull", fullPull).put("pushBlocked", blocked)
        }
        store.write("yoyo_cache.json", o)
    }

    /** One sync cycle now (tests). */
    internal suspend fun runCycle(check: Boolean = false) = cycle(check)

    private suspend fun cycle(check: Boolean) {
        if (!signedIn) { events.status("not signed in"); return }
        try {
            val stale = System.currentTimeMillis() - fullPull > FULL_PULL_EVERY
            if (check && !stale && known.isNotEmpty() && changedOnYoyo()) {
                events.status("out of date")
                wantFull = true
            }
            push()
            if (wantDue) {  // after the push, so cards just answered aren't still listed as due
                wantDue = false
                val fetched = System.currentTimeMillis()
                events.due(due(), fetched)
            }
            if (wantFull || stale || cards.isEmpty()) {
                wantFull = false
                pull()
            }
            events.status(if (blocked.isNotEmpty()) "blocked" else "up to date")
        } catch (e: AuthError) {
            events.status("expired")
        } catch (e: Exception) {  // offline, server error: keep the answers and try again in a minute
            events.status("offline")
            scope.launch { delay(60_000); kick.trySend(0) }
        }
    }

    // ---------- HTTP

    private fun http(path: String, body: JSONObject, session: String?) = transport(path, body, session)

    private fun login() {
        val (email, password) = credentials ?: throw AuthError("not signed in")
        val res = http("/auth/login", JSONObject().put("email", email).put("password", password), null)
        if (res.status >= 400) throw AuthError("sign-in failed (${res.status})")
        // The session is the signed "token" cookie; the body's plain token isn't accepted for page data.
        val t = Regex("""(?:^|[;,\s])token=([^;,\s]+)""").find(res.setCookie)?.groupValues?.get(1)
            ?: throw AuthError("sign-in gave no session")
        setToken(t)
    }

    private fun post(path: String, body: JSONObject = JSONObject(), retried: Boolean = false): JSONObject {
        if (token.isEmpty()) login()
        val res = http(path, body, token)
        // signed-out requests don't always fail: pages come back empty and me/provider says !isValid
        val emptyPage = path.startsWith("/page/") && res.status == 200 && res.data?.optJSONObject("pageData")?.length() == 0
        val expired = res.status == 401 || res.status == 403 || emptyPage ||
            (path == "/me/provider" && res.data?.optJSONObject("meData")?.optBoolean("isValid") == false)
        if (expired) {
            if (retried || credentials == null) throw AuthError("YoYo answered ${res.status}")
            setToken("")
            login()  // session expired: sign in again with the saved password
            return post(path, body, true)
        }
        if (res.status >= 400) throw Exception("YoYo answered ${res.status} for $path")
        return res.data ?: JSONObject()
    }

    private fun me(): JSONObject {
        val me = post("/me/provider").optJSONObject("meData") ?: JSONObject()
        if (!me.optBoolean("isValid")) throw AuthError("not signed in")
        return me
    }

    private fun cardsPage(filters: JSONObject, page: Int = 1): JSONObject {
        val data = post("/flashcards/manage/cards", JSONObject().put("filters", filters).put("page", page).put("cardsPerPage", PAGE_SIZE))
        if (!data.has("flashcards")) throw AuthError("could not read flashcards")
        return data
    }

    /** One card filter per lesson: groups small enough for a single page (paging is unstable). */
    private fun lessonFilters(): List<JSONObject> {
        val opts = post("/page/flashcards-mastery-manage_page").getJSONObject("pageData").getJSONObject("filterOptions")
        fun byValue(key: String) = opts.getJSONArray(key).let { a ->
            (0 until a.length()).map { a.getJSONObject(it) }.filter { it.optString("value").isNotEmpty() }.associateBy { it.getString("value") }
        }
        val units = byValue("unitOptions")
        val levels = byValue("levelOptions")
        val lessonOpts = opts.getJSONArray("lessonOptions")
        return (0 until lessonOpts.length()).mapNotNull { i ->
            val lesson = lessonOpts.getJSONObject(i)
            val unit = units[lesson.optString("unitId")]
            if (lesson.optString("value").isEmpty() || unit == null) return@mapNotNull null
            val level = levels[unit.optString("levelId")]
            JSONObject().put("masteryType", "all").put("courseId", level?.optString("courseId") ?: "")
                .put("levelId", unit.optString("levelId")).put("unitId", unit.getString("value")).put("lessonId", lesson.getString("value"))
        }
    }

    private fun flashcards(data: JSONObject) = data.getJSONArray("flashcards").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

    private fun fetchGroup(filters: JSONObject): List<JSONObject> {
        val first = cardsPage(filters)
        val total = first.optInt("totalFlashcards")
        if (total <= PAGE_SIZE) return flashcards(first)
        if (filters.getString("masteryType") == "all") {  // split the group so each half may fit one page
            return listOf("mastered", "not-mastered").flatMap { fetchGroup(JSONObject(filters.toString()).put("masteryType", it)) }
        }
        val seen = LinkedHashMap<String, JSONObject>()
        flashcards(first).forEach { seen[it.getString("_id")] = it }
        val pages = (total + PAGE_SIZE - 1) / PAGE_SIZE
        repeat(UNION_ROUNDS) {  // re-read until every card turned up
            for (p in 1..pages) flashcards(cardsPage(filters, p)).forEach { seen[it.getString("_id")] = it }
            if (seen.size >= total) return seen.values.toList()
        }
        throw Exception("could only read ${seen.size} of $total cards in a lesson")
    }

    /** All cards (or one course level's) with their YoYo levels, read lesson by lesson. */
    private suspend fun fetch(levelId: String = ""): List<JSONObject> = coroutineScope {
        if (lessons.isEmpty()) lessons = lessonFilters()
        val groups = lessons.filter { levelId.isEmpty() || it.getString("levelId") == levelId }
        val gate = Semaphore(WORKERS)
        val results = groups.map { f -> async(Dispatchers.IO) { gate.withPermit { fetchGroup(f) } } }.awaitAll()
        results.flatten().associateBy { it.getString("_id") }.values.toList()
    }

    private val JSONObject.level get() = if (has("masteryLevel") && !isNull("masteryLevel")) getInt("masteryLevel") else -1

    private suspend fun pull(levelId: String = "", fetched: List<JSONObject>? = null) {
        val started = System.currentTimeMillis()
        val list = fetched ?: run {
            if (levelId.isEmpty()) { events.status("downloading"); lessons = emptyList() }
            fetch(levelId)
        }
        val levels = HashMap<String, Int>()
        synchronized(lock) {
            for (c in list) {
                val code = c.optString("code")
                if (code.isEmpty()) continue
                cards[code] = CardRef(c.getString("_id"), c.optString("levelId"))
                levels[code] = c.level
            }
        }
        known.putAll(levels)
        if (levelId.isEmpty()) fullPull = started
        saveCache()
        events.levels(levels, started, levelId.isEmpty())
    }

    /** Upload batches that name each card at most once: answering a card again (★ Master sends
     *  Good three times) goes in the next upload, so YoYo counts every answer. */
    private fun uploads(byLevel: Map<String, List<Pair<Pending, String>>>): List<Pair<String, List<Pair<Pending, String>>>> =
        byLevel.flatMap { (levelId, items) ->
            val rounds = mutableListOf<MutableList<Pair<Pending, String>>>()
            for (item in items) {
                val r = rounds.firstOrNull { r -> r.none { it.second == item.second } } ?: mutableListOf<Pair<Pending, String>>().also { rounds += it }
                r += item
            }
            rounds.map { levelId to it }
        }

    private suspend fun push() {
        if (blocked.isNotEmpty()) return
        val byLevel = LinkedHashMap<String, MutableList<Pair<Pending, String>>>()
        val unknown = mutableListOf<Pending>()
        synchronized(lock) {
            for (item in pending) {
                val card = cards[item.code]
                if (card != null && card.levelId.isNotEmpty()) byLevel.getOrPut(card.levelId) { mutableListOf() } += item to card.id
                else if (item.t < fullPull) unknown += item  // a full pull since the answer didn't find it on YoYo
            }
        }
        for ((levelId, items) in uploads(byLevel)) {
            val before = fetch(levelId).associate { it.getString("code") to it.level }
            post("/results/flashcard-mastery", JSONObject()
                .put("responses", JSONArray().also { a -> items.forEach { (p, id) -> a.put(JSONObject().put("flashcardId", id).put("answer", p.answer)) } })
                .put("objectType", "level").put("objectId", levelId))
            synchronized(lock) { items.forEach { (p, _) -> pending.remove(p) } }
            saveCache()
            val after = fetch(levelId)
            val answered = items.map { it.first.code }.toSet()
            val dropped = after.filter { it.getString("code") !in answered && it.level < (before[it.getString("code")] ?: -1) }.map { it.getString("code") }
            pull(levelId, after)
            if (dropped.isNotEmpty()) {
                blocked = "${dropped.size} card(s) you didn't answer lost level (${dropped.take(5).joinToString(", ")})"
                saveCache()
                return
            }
        }
        if (unknown.isNotEmpty()) {
            synchronized(lock) { pending.removeAll(unknown.toSet()) }
            saveCache()
        }
    }

    /** Cards YoYo's spaced-repetition schedule says are due, the way its website builds a review deck. */
    private fun due(): List<String> {
        val opts = post("/page/flashcards-mastery-manage_page").getJSONObject("pageData").getJSONObject("filterOptions")
        val out = mutableListOf<String>()
        val courses = opts.getJSONArray("courseOptions")
        for (i in 0 until courses.length()) {
            val cid = courses.getJSONObject(i).optString("value")
            if (cid.isEmpty()) continue
            val decks = post("/page/flashcards-mastery-decks_page", JSONObject()
                .put("pageUrl", "/mastery/flashcards/decks/course/$cid").put("objectType", "course").put("objectId", cid))
                .optJSONObject("pageData")?.optJSONArray("decks") ?: continue
            for (j in 0 until decks.length()) {
                val deck = decks.getJSONObject(j)
                if (deck.optInt("totalDue") == 0 || deck.optBoolean("isLocked")) continue
                val lid = deck.getString("levelId")
                val list = post("/page/flashcards-mastery-deck_page", JSONObject()
                    .put("pageUrl", "/mastery/flashcards/level/$lid").put("objectType", "level").put("objectId", lid))
                    .optJSONObject("pageData")?.optJSONArray("flashcards") ?: continue
                for (k in 0 until list.length()) list.getJSONObject(k).let { if (it.level >= 0) out += it.getString("code") }  // reviews, not new
            }
        }
        return out
    }

    /** Cheap check: has your mastered count on YoYo moved since the last download? */
    private fun changedOnYoyo(): Boolean = me().optInt("masteredFlashcards") != known.values.count { it >= MASTERED }
}

/** The real network: YoYo's API over HTTPS. */
fun httpPost(path: String, body: JSONObject, session: String?): Resp {
    val c = URL(API + path).openConnection() as HttpURLConnection
    try {
        c.requestMethod = "POST"
        c.connectTimeout = 20_000
        c.readTimeout = 40_000
        c.doOutput = true
        c.instanceFollowRedirects = false
        c.setRequestProperty("Content-Type", "application/json")
        c.setRequestProperty("is-native", "false")
        if (session != null) c.setRequestProperty("Cookie", "token=$session")
        c.outputStream.use { it.write(body.toString().toByteArray()) }
        val status = c.responseCode
        val text = (if (status >= 400) c.errorStream else c.inputStream)?.bufferedReader()?.use { it.readText() } ?: ""
        val setCookie = c.headerFields.filterKeys { it.equals("Set-Cookie", ignoreCase = true) }.values.flatten().joinToString(";")
        return Resp(status, runCatching { JSONObject(text) }.getOrNull(), setCookie)
    } finally {
        c.disconnect()
    }
}
