package com.sidebarstudy.wear

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

const val LANG = "zh-CN"

fun recognizerIntent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, LANG)
    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, LANG)
    .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
    .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
    .putExtra(RecognizerIntent.EXTRA_PROMPT, "Say it in Mandarin")

/**
 * Listening like the phone app: a tap opens a window (a minute by default) that starts over every
 * time you speak, and the recognizer is restarted phrase after phrase until the window closes.
 * Uses the watch's own speech recognizer; watches without one fall back to the system voice
 * input screen, one phrase per tap (see [inline]).
 */
class Listener(context: Context, private val h: Handlers) {
    interface Handlers {
        fun partial(matches: List<String>)
        fun final(matches: List<String>)
        fun state(listening: Boolean)
        fun error(message: String)
    }

    /** False when the watch has no recognizer the app can use directly. */
    val inline = SpeechRecognizer.isRecognitionAvailable(context)
    private val main = Handler(Looper.getMainLooper())
    private val recognizer = if (inline) SpeechRecognizer.createSpeechRecognizer(context) else null
    private var windowMs = 60_000L
    private var deadline = 0L
    private var running = false
    private var paused = false
    private var startedAt = 0L
    private var heardAny = false

    val open get() = deadline > System.currentTimeMillis()

    init {
        recognizer?.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() { heardSpeech() }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
            override fun onPartialResults(partialResults: Bundle?) {
                val m = matches(partialResults)
                if (m.isNotEmpty() && running) { heardAny = true; heardSpeech(); h.partial(m) }
            }
            override fun onResults(results: Bundle?) {
                val m = matches(results)
                running = false
                if (m.isNotEmpty()) { heardSpeech(); h.final(m) }
                next(250)
            }
            override fun onError(error: Int) {
                if (!running) return
                running = false
                if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) { h.error("Microphone permission is off."); close(); return }
                // a session that dies instantly never really started: give the recognizer a breather
                next(if (System.currentTimeMillis() - startedAt < 400 && !heardAny) 900 else 250)
            }
        })
    }

    private fun matches(b: Bundle?) =
        b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.filter { it.isNotBlank() } ?: emptyList()

    fun openWindow(ms: Long) {
        windowMs = ms
        deadline = System.currentTimeMillis() + ms
        h.state(true)
        watch()
        start()
    }

    fun close() {
        deadline = 0
        stopSession()
        h.state(false)
    }

    private fun heardSpeech() { if (open) deadline = System.currentTimeMillis() + windowMs }

    fun pause() { paused = true; stopSession() }
    fun resume() { paused = false; start() }

    /** Throw away whatever was heard so far (a new card is showing). */
    fun restart() { stopSession(); next(150) }

    /** The guesses already match: ask for the final guesses now instead of waiting out the silence timeout. */
    fun finishPhrase() { if (running) recognizer?.stopListening() }

    private fun watch() {
        main.removeCallbacks(tick)
        main.postDelayed(tick, 1000)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (deadline == 0L) return
            if (!open) { close(); return }
            main.postDelayed(this, 1000)
        }
    }

    private fun next(delayMs: Long) {
        main.removeCallbacks(startRunnable)
        main.postDelayed(startRunnable, delayMs)
    }

    private val startRunnable = Runnable { start() }

    private fun start() {
        val r = recognizer ?: return
        if (running || paused || !open) return
        running = true
        heardAny = false
        startedAt = System.currentTimeMillis()
        r.startListening(recognizerIntent())
    }

    private fun stopSession() {
        main.removeCallbacks(startRunnable)
        if (running) { running = false; recognizer?.cancel() }
    }

    fun destroy() { main.removeCallbacksAndMessages(null); recognizer?.destroy() }
}

/** Reads the card aloud with the watch's Chinese voice, if it has one. */
class Speaker(context: Context) {
    private var ready = false
    var available = false; private set
    private var onDone: (() -> Unit)? = null
    private val main = Handler(Looper.getMainLooper())
    private val tts: TextToSpeech = TextToSpeech(context) { status ->
        ready = status == TextToSpeech.SUCCESS
        available = ready && tts.setLanguage(Locale.SIMPLIFIED_CHINESE) >= TextToSpeech.LANG_AVAILABLE
        tts.setSpeechRate(0.85f)
    }

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) { main.post { onDone?.invoke(); onDone = null } }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { main.post { onDone?.invoke(); onDone = null } }
        })
    }

    /** False when there's no Chinese voice (done is not called). */
    fun say(text: String, done: () -> Unit): Boolean {
        if (!available) return false
        onDone = done
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "card")
        return true
    }

    fun shutdown() = tts.shutdown()
}
