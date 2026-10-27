package com.sidebarstudy.wear

import android.app.Application
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Tone { DIM, INK, GREEN, RED, ORANGE, YELLOW, PINK, VIOLET, TEAL }

/** What the characters show: which are right, the pinyin above, and (after an answer) yours below. */
data class Hz(
    val matched: Set<Int> = emptySet(),
    val final: Boolean = false,
    val ruby: Map<Int, String> = emptyMap(),
    val rubyTone: Tone? = null,
    val toneOff: Set<Int> = emptySet(),
    val reveal: Boolean = false,
    val said: Map<Int, Pair<String, String>>? = null,
    val text: String? = null,  // shown instead of the card (⏳, 🎉)
)

private const val NEW_PER_ROUND = 25      // new cards per round, like a YoYo deck
private const val DUE_WAIT_MS = 30_000L   // wait this long for YoYo's due list before the offline order
private const val SETTLE_MS = 700L        // live guess matches every character: end the phrase after this much quiet
const val ALL_DECKS = "All decks"

fun stripHanzi(s: String) = s.filter { it in '一'..'鿿' }

/** The phone app's card flow (app.js), for the watch. */
@OptIn(ExperimentalCoroutinesApi::class)
class StudyModel(app: Application) : AndroidViewModel(app), YoyoSync.Events, Listener.Handlers {
    private val store = Store(app)
    private val prefs = store.prefs
    private val grader = Grader(app)
    private val speaker = Speaker(app)
    val listener = Listener(app, this)
    private val sync = YoyoSync(store, viewModelScope, this)
    private val main = Handler(Looper.getMainLooper())
    private val serial = Dispatchers.Default.limitedParallelism(1)  // grading in the order things were heard

    // ---------- settings
    var deck by mutableStateOf(prefs.getString("deck", ALL_DECKS) ?: ALL_DECKS); private set
    var delaySec by mutableStateOf(prefs.getFloat("delay", 3f)); private set
    var showEnglish by mutableStateOf(prefs.getBoolean("showEnglish", false)); private set
    var reviewMastered by mutableStateOf(prefs.getBoolean("reviewMastered", false)); private set
    var listenWindow by mutableIntStateOf(prefs.getInt("listenWindow", 60)); private set
    var email by mutableStateOf(prefs.getString("email", "") ?: ""); private set

    // ---------- what the screen shows
    var deckNames by mutableStateOf(listOf(ALL_DECKS)); private set
    var card by mutableStateOf<Card?>(null); private set
    var layout by mutableStateOf<Layout?>(null); private set
    var hz by mutableStateOf(Hz(text = "⏳")); private set
    var english by mutableStateOf(""); private set
    var status by mutableStateOf("Loading cards…"); private set
    var statusTone by mutableStateOf(Tone.DIM); private set
    var level by mutableIntStateOf(-1); private set
    var listening by mutableStateOf(false); private set
    var syncState by mutableStateOf(""); private set
    var pending by mutableIntStateOf(0); private set
    var right by mutableIntStateOf(0); private set
    var passed by mutableIntStateOf(0); private set
    var streak by mutableIntStateOf(0); private set
    var flash by mutableStateOf(0 to Tone.GREEN); private set  // (count, color): each change flashes the ring
    val signedIn get() = sync.signedIn
    val blocked get() = sync.blocked
    val inlineMic get() = listener.inline

    // ---------- card state
    private var decks = LinkedHashMap<String, List<Card>>()
    private val allCards = HashMap<String, Card>()
    private val progress = store.loadProgress()
    private var queue = ArrayDeque<Card>()
    private var locked = false; private var solved = false; private var mustRepeat = false; private var helped = false
    private var said: Map<Int, Pair<String, String>>? = null
    private var revealHz: Hz? = null
    private var passedAt = 0L
    private var advanceJob: Job? = null
    private var settleJob: Job? = null
    private var due: List<String>? = null
    private var dueTime = 0L
    private var roundRequested = false
    private var speaking = false

    init {
        viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) { Decks.load(app) }
            withContext(serial) { loaded.values.firstOrNull()?.firstOrNull()?.let { grader.layout(it) } }  // warm up QuickJS
            decks = loaded
            for (cards in loaded.values) for (c in cards) {
                allCards[c.code] = c
                if (c.mastered && c.code !in progress) progress[c.code] = Progress(level = MASTERED)
            }
            deckNames = listOf(ALL_DECKS) + loaded.keys
            if (deck !in deckNames) deck = ALL_DECKS
            if (allCards.isEmpty()) { showLoading("No cards in this build: run build-wear.bat with your decks in flashcards/."); return@launch }
            val password = if (email.isNotEmpty()) store.password else null
            if (password != null) sync.credentials = email to password
            if (sync.signedIn) {
                main.postDelayed({ dueFallback("YoYo didn't answer") }, DUE_WAIT_MS)
                nextCard()  // shows "Asking YoYo which cards are due…" until the list arrives
            } else {
                rebuildQueue()
                nextCard()
            }
            pending = sync.pendingCount
            sync.start()
        }
    }

    // ---------- levels and queue

    private fun level(code: String) = progress[code]?.level ?: -1
    private fun isMastered(code: String) = level(code) >= MASTERED
    private fun inProgress(code: String): Boolean {
        val p = progress[code] ?: return false
        return (p.level >= 0 || p.ok > 0 || p.fail > 0) && p.level < MASTERED
    }
    private fun deckCards() = decks[deck] ?: decks.values.flatten()

    private fun <K> shuffledGroups(items: List<Card>, key: (Card) -> K, sort: Comparator<K>? = null): List<Card> {
        val groups = LinkedHashMap<K, MutableList<Card>>()
        for (c in items) groups.getOrPut(key(c)) { mutableListOf() } += c
        val keys = if (sort != null) groups.keys.sortedWith(sort) else groups.keys.toList()
        return keys.flatMap { groups.getValue(it).shuffled() }
    }

    /** With YoYo: its due reviews, then new cards from your current level in lesson order.
     *  Offline: cards you've started (lowest level first), then new cards in course order. */
    private fun rebuildQueue() {
        val cards = deckCards()
        val current = card?.code
        val q = mutableListOf<Card>()
        val dueList = due
        if (dueList != null) {
            val inDeck = cards.associateBy { it.code }
            val answered = progress.filter { it.value.last > dueTime }.keys
            q += dueList.distinct().filter { it in inDeck && it !in answered }.map { inDeck.getValue(it) }
            val fresh = cards.filter { level(it.code) < 0 }
            fun levelOf(c: Card) = sync.levelIdOf(c.code) ?: c.code.substringBefore("-")
            val focus = fresh.firstOrNull()?.let { levelOf(it) }  // one level at a time keeps you on a unit
            q += shuffledGroups(fresh.filter { levelOf(it) == focus }, { it.lesson }).take(NEW_PER_ROUND)
        } else {
            val started = cards.filter { inProgress(it.code) }
            val fresh = cards.filter { !isMastered(it.code) && !inProgress(it.code) }
            q += shuffledGroups(started, { maxOf(level(it.code), 0) }, naturalOrder())
            q += shuffledGroups(fresh, { it.lesson })
        }
        if (reviewMastered) q += cards.filter { isMastered(it.code) }.shuffled()
        queue = ArrayDeque(q.filter { it.code != current })
    }

    // ---------- YoYo events (from the sync's thread)

    override fun status(state: String) {
        main.post {
        syncState = state
        pending = sync.pendingCount
        if (state in listOf("offline", "expired", "not signed in")) dueFallback(state)
        }
    }

    override fun levels(levels: Map<String, Int>, startedAt: Long, full: Boolean) {
        main.post {
        var changed = false
        for ((code, lv) in levels) {
            if (code !in allCards) continue
            val p = progress.getOrPut(code) { Progress() }
            if (p.last > startedAt || p.level == lv) continue
            p.level = lv
            changed = true
        }
        if (changed) {
            store.saveProgress(progress)
            if (full && due == null) rebuildQueue()
            card?.let { if (!locked) level = level(it.code) }
        }
        }
    }

    override fun due(codes: List<String>, fetchedAt: Long) {
        main.post {
        due = codes
        dueTime = fetchedAt
        rebuildQueue()
        if (card == null) { if (queue.isNotEmpty()) nextCard() else showCard() }
        }
    }

    private fun dueFallback(reason: String) {
        if (due != null || card != null || allCards.isEmpty()) return
        roundRequested = true  // don't keep asking; Sync now or the next round will
        rebuildQueue()
        nextCard()
        if (card != null) setStatus("Offline order ($reason)", Tone.ORANGE)
    }

    private fun record(ok: Boolean, easy: Boolean = false) {
        val c = card ?: return
        val answer = if (!ok) "hard" else if (easy) "easy" else "good"
        val p = progress.getOrPut(c.code) { Progress() }
        if (ok) p.ok++ else p.fail++
        p.last = System.currentTimeMillis()
        p.level = nextLevel(p.level, answer)
        store.saveProgress(progress)
        sync.record(c.code, answer)
        pending = sync.pendingCount
        level = p.level
    }

    // ---------- card flow

    fun nextCard() {
        advanceJob?.cancel(); settleJob?.cancel()
        locked = false; solved = false; mustRepeat = false; helped = false; said = null; revealHz = null
        if (queue.isEmpty() && sync.signedIn && !roundRequested) {
            // round finished (or just started): send the answers, then ask YoYo what's due now
            roundRequested = true
            val finished = card != null
            card = null
            showLoading(if (finished) "Round done ✓ — asking YoYo what's due…" else "Asking YoYo which cards are due…")
            sync.requestDue()
            return
        }
        val next = queue.removeFirstOrNull()
        if (next != null) roundRequested = false
        layout = next?.let { grader.layout(it) }
        card = next
        if (listener.open) listener.restart()
        showCard()
    }

    private fun showCard() {
        val c = card
        if (c == null) {
            hz = Hz(text = "🎉")
            english = "All caught up — nothing due on YoYo and no new cards here."
            setStatus("")
            return
        }
        hz = Hz()
        level = level(c.code)
        english = if (showEnglish) c.english else ""
        setStatus("")
    }

    private fun showLoading(text: String) {
        hz = Hz(text = "⏳")
        english = ""
        setStatus(text)
    }

    private fun setStatus(text: String, tone: Tone = Tone.DIM) { status = text; statusTone = tone }

    private fun allChars() = layout?.answer?.keys ?: emptySet()

    private fun finish(ok: Boolean, message: String, grade: String? = null) {
        val c = card ?: return
        val answer = layout?.answer ?: emptyMap()
        solved = true
        english = c.english
        if (!ok) {  // passed: reveal, and stay until it's said correctly (YoYo decides when it's due again)
            record(false)
            mustRepeat = true
            hz = Hz(ruby = answer, rubyTone = Tone.PINK, reveal = true, said = said).also { revealHz = it }
            setStatus(message, Tone.PINK)
            streak = 0
            passed++
            signal(Tone.PINK)
            return
        }
        locked = true
        var msg = message
        if (!mustRepeat) {  // repeating a passed card doesn't count as mastering it
            val easy = grade?.let { it == "easy" } ?: !helped
            record(true, easy)
            msg += if (easy) "  · Good +2" else "  · Not Bad +1"
            streak++
            right++
            if (streak % 5 == 0) msg += "  🔥 $streak"
        }
        hz = Hz(matched = allChars(), ruby = answer, rubyTone = Tone.GREEN, reveal = true, said = said)
        setStatus(msg, Tone.GREEN)
        signal(Tone.GREEN)
        advanceJob = viewModelScope.launch { delay((delaySec * 1000).toLong()); nextCard() }
    }

    private fun toneSlip(r: Grade) {
        // right sounds, wrong tone: not a miss — you just say it again (nothing recorded yet)
        solved = true
        helped = true
        english = card?.english ?: ""
        hz = Hz(matched = allChars(), ruby = layout?.answer ?: emptyMap(), rubyTone = Tone.GREEN, toneOff = r.toneOff,
            reveal = true, said = said).also { revealHz = it }
        setStatus("Tone off — say it again\n" + r.slips.joinToString("\n"), Tone.YELLOW)
        signal(Tone.ORANGE)
    }

    /** Saying "pass": fails the card, or, once it's been answered or passed, moves on. */
    private fun pass() {
        if (card == null) return
        if (locked || mustRepeat) {
            if (System.currentTimeMillis() - passedAt > 1000) nextCard()
            return
        }
        passedAt = System.currentTimeMillis()
        finish(false, "Passed ✗ — say it, or tap a grade to move on")
    }

    /** The Good / Not Bad / Forgot buttons, like YoYo's. Once the card is graded, any of them moves on. */
    fun grade(answer: String) {
        if (card == null) return
        if (locked || mustRepeat) {
            if (System.currentTimeMillis() - passedAt > 1000) nextCard()  // a double tap shouldn't skip the answer
            return
        }
        if (listener.open) listener.restart()
        if (answer == "hard") {
            passedAt = System.currentTimeMillis()
            finish(false, "Forgot ✗ — say it, or tap a grade to move on")
        } else {
            finish(true, "✓ Graded", answer)
        }
    }

    /** ★ Master (after the screen's confirmation): Good three times, which takes any card to mastered. */
    fun master() {
        val c = card ?: return
        advanceJob?.cancel(); settleJob?.cancel()
        repeat(3) { record(true, easy = true) }
        locked = true; solved = true
        english = c.english
        hz = Hz(matched = allChars(), ruby = layout?.answer ?: emptyMap(), rubyTone = Tone.GREEN, reveal = true)
        setStatus("★ Mastered — Good ×3 sent to YoYo", Tone.YELLOW)
        signal(Tone.GREEN)
        if (listener.open) listener.restart()
        advanceJob = viewModelScope.launch { delay((delaySec * 1000).toLong()); nextCard() }
    }

    // ---------- hearing results

    override fun partial(matches: List<String>) {
        settleJob?.cancel()
        val c = card ?: return
        if (locked || speaking) return
        viewModelScope.launch {
            val r = withContext(serial) { grader.grade(c, matches) } ?: return@launch
            if (card !== c || locked) return@launch
            if (r.ok && r.toneOk) {
                said = r.said
                finish(true, "✓")  // the pinyin you said is drawn over the characters
                listener.restart()
                return@launch
            }
            // every character is there but it isn't certain yet (usually a tone): once you stop
            // talking, grade the final guesses right away instead of after the long silence timeout
            if (r.ok || r.matched.size == allChars().size) {
                settleJob?.cancel()
                settleJob = viewModelScope.launch { delay(SETTLE_MS); if (!locked) listener.finishPhrase() }
            }
            if (!solved) hz = Hz(matched = r.matched, ruby = r.ruby, toneOff = r.toneOff)
            setStatus("… ${r.shown}", Tone.VIOLET)
        }
    }

    override fun final(matches: List<String>) {
        settleJob?.cancel()
        val c = card ?: return
        if (locked || speaking) return
        viewModelScope.launch {
            val isPass = withContext(serial) { grader.isPass(matches) }
            if (card !== c || locked) return@launch
            if (isPass) { pass(); return@launch }
            val r = withContext(serial) { grader.grade(c, matches) } ?: return@launch
            if (card !== c || locked) return@launch
            said = r.said
            val shown = r.shown  // (no pinyin: it's drawn over the characters, and a watch has little room)
            if (r.ok && !r.toneOk) { toneSlip(r); return@launch }
            if (r.ok) { finish(true, "✓"); return@launch }
            helped = true  // wrong guesses cost nothing, but it's no longer a first try
            hz = if (!solved) Hz(matched = r.matched, final = true, ruby = r.ruby, toneOff = r.toneOff)
            else (revealHz ?: hz).copy(reveal = true, said = said)  // keep the answer up, your attempt under it
            setStatus("heard $shown — try again", Tone.ORANGE)
            signal(Tone.ORANGE)
        }
    }

    override fun state(listening: Boolean) { this.listening = listening }
    override fun error(message: String) = setStatus(message, Tone.RED)

    /** Watches without an inline recognizer: the system voice screen's result. */
    fun heardFromSystem(matches: List<String>) = final(matches)

    /** Debug builds: show the first card containing [text] next. */
    fun debugShow(text: String) {
        val c = allCards.values.firstOrNull { it.simplified.contains(text) } ?: return
        queue.addFirst(c)
        nextCard()
    }

    fun toggleMic() {
        if (listener.open) listener.close() else listener.openWindow(listenWindow * 1000L)
    }

    // ---------- read aloud

    fun speak() {
        val c = card ?: return
        if (!locked) helped = true  // a hint, so no Good +2
        speaking = true
        listener.pause()  // the mic mustn't grade the watch's own voice
        val started = speaker.say(stripHanzi(c.simplified).ifEmpty { c.simplified }) {
            main.postDelayed({ speaking = false; listener.resume() }, 600)  // echo tail
        }
        if (!started) {
            speaking = false
            listener.resume()
            setStatus("No Chinese voice on this watch: add one in Settings → General → Text-to-speech.", Tone.RED)
        }
    }

    // ---------- feedback

    private fun signal(tone: Tone) {
        flash = (flash.first + 1) to tone
        val vib = vibrator() ?: return
        val effect = when (tone) {
            Tone.GREEN -> VibrationEffect.createPredefined(VibrationEffect.EFFECT_CLICK)
            else -> VibrationEffect.createPredefined(VibrationEffect.EFFECT_DOUBLE_CLICK)
        }
        runCatching { vib.vibrate(effect) }
    }

    private fun vibrator(): Vibrator? {
        val ctx = getApplication<Application>()
        return if (Build.VERSION.SDK_INT >= 31) (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        else @Suppress("DEPRECATION") (ctx.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator)
    }

    // ---------- settings and account

    fun chooseDeck(name: String) { deck = name; prefs.edit().putString("deck", name).apply(); rebuildQueue(); nextCard() }
    fun setDelay(s: Float) { delaySec = s; prefs.edit().putFloat("delay", s).apply() }
    fun changeListenWindow(s: Int) { listenWindow = s; prefs.edit().putInt("listenWindow", s).apply() }
    fun toggleShowEnglish() {
        showEnglish = !showEnglish; prefs.edit().putBoolean("showEnglish", showEnglish).apply()
        card?.let { if (!solved) english = if (showEnglish) it.english else "" }
    }
    fun toggleReviewMastered() {
        reviewMastered = !reviewMastered; prefs.edit().putBoolean("reviewMastered", reviewMastered).apply()
        rebuildQueue(); if (!solved) nextCard()
    }

    fun signIn(newEmail: String, password: String) {
        email = newEmail
        prefs.edit().putString("email", newEmail).apply()
        store.password = password
        sync.signIn(newEmail to password)
        syncState = "checking"
        roundRequested = false
        sync.requestDue()
    }

    fun signOut() { store.password = null; sync.signOut() }
    fun syncNow() { sync.syncNow(full = true); sync.requestDue() }

    override fun onCleared() {
        listener.destroy()
        speaker.shutdown()
    }
}
