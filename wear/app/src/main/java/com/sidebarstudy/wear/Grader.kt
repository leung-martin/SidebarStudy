package com.sidebarstudy.wear

import android.content.Context
import com.whl.quickjs.android.QuickJSLoader
import com.whl.quickjs.wrapper.QuickJSContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** One verdict on what was heard (see watch-bridge.js `SS.grade`). */
data class Grade(
    val ok: Boolean,
    val toneOk: Boolean,
    val text: String,
    val heardPy: String,
    val shown: String,
    val matched: Set<Int>,
    val toneOff: Set<Int>,
    val ruby: Map<Int, String>,
    val said: Map<Int, Pair<String, String>>,  // per hanzi: (pinyin you said, ok|tone|bad|skip)
    val slips: List<String>,                    // "试 shì — 4th tone (you said 2nd)"
)

/** A card split for drawing: each part is a hanzi (k = its index) or other text (k = -1). */
data class Layout(val parts: List<Pair<String, Int>>, val answer: Map<Int, String>)

/**
 * The phone app's grading (grading.js + pinyin-pro, bundled as assets/grading.js by
 * `npm run build:watch`), run in QuickJS so the watch grades exactly like the phone.
 * QuickJS is single-threaded: every call goes through one worker thread.
 */
class Grader(context: Context) {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var js: QuickJSContext

    init {
        val script = context.assets.open("grading.js").bufferedReader().use { it.readText() }
        worker.execute {
            QuickJSLoader.init()
            js = QuickJSContext.create()
            js.evaluate(script, "grading.js")
        }
    }

    private fun call(expr: String): String = worker.submit<String> { js.evaluate(expr).toString() }.get()

    private fun q(s: String) = JSONObject.quote(s)

    /** Blocking: call off the main thread. */
    fun grade(card: Card, heard: List<String>): Grade? {
        val out = call("SS.grade(${q(card.json())}, ${q(JSONArray(heard).toString())})")
        if (out == "null") return null
        val o = JSONObject(out)
        fun ints(key: String) = o.getJSONArray(key).let { a -> (0 until a.length()).map { a.getInt(it) }.toSet() }
        val ruby = o.getJSONObject("ruby").let { r -> r.keys().asSequence().associate { it.toInt() to r.getString(it) } }
        val said = o.getJSONObject("said").let { s ->
            s.keys().asSequence().associate { k -> s.getJSONArray(k).let { k.toInt() to (it.getString(0) to it.getString(1)) } }
        }
        val slips = o.getJSONArray("slips").let { a -> (0 until a.length()).map { a.getString(it) } }
        return Grade(o.getBoolean("ok"), o.getBoolean("toneOk"), o.getString("text"), o.getString("heardPy"),
            o.getString("shown"), ints("matched"), ints("toneOff"), ruby, said, slips)
    }

    fun isPass(heard: List<String>): Boolean = call("SS.isPass(${q(JSONArray(heard).toString())})") == "true"

    fun layout(card: Card): Layout {
        val o = JSONObject(call("SS.answer(${q(card.json())})"))
        val parts = o.getJSONArray("parts").let { a ->
            (0 until a.length()).map { a.getJSONObject(it).let { p -> p.getString("ch") to p.getInt("k") } }
        }
        val ruby = o.getJSONObject("ruby").let { r -> r.keys().asSequence().associate { it.toInt() to r.getString(it) } }
        return Layout(parts, ruby)
    }
}
