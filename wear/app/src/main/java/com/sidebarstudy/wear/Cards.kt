package com.sidebarstudy.wear

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class Card(
    val code: String,
    val simplified: String,
    val traditional: String,
    val pinyin: String,
    val english: String,
    val mastered: Boolean,
) {
    /** ICC-011-01-004 -> ICC-011-01 */
    val lesson: String get() = code.substringBeforeLast("-")

    fun json(): String = JSONObject()
        .put("code", code).put("simplified", simplified).put("traditional", traditional)
        .put("pinyin", pinyin).put("english", english).toString()
}

/** The YoYo decks bundled in assets/decks (copied from flashcards/ by build-wear.bat), in course order. */
object Decks {
    private val FILES = listOf(
        "yoyochinese_beginner_conversational_flashcards.json" to "Beginner Conversational",
        "yoyochinese_intermediate_conversational_flashcards.json" to "Intermediate Conversational",
        "yoyochinese_upper_intermediate_conversational_flashcards.json" to "Upper Intermediate Conversational",
        "yoyochinese_advanced_conversational_chinese_flashcards.json" to "Advanced Conversational Chinese",
        "yoyochinese_chinese_characters_flashcards.json" to "Chinese Characters",
        "yoyochinese_chinese_characters_ii_flashcards.json" to "Chinese Characters II",
        "yoyochinese_chinese_character_reader_flashcards.json" to "Chinese Character Reader",
    )

    fun load(context: Context): LinkedHashMap<String, List<Card>> {
        val decks = LinkedHashMap<String, List<Card>>()
        val present = context.assets.list("decks")?.toSet() ?: emptySet()
        for ((file, name) in FILES) {
            if (file !in present) continue
            val a = JSONArray(context.assets.open("decks/$file").bufferedReader().use { it.readText() })
            decks[name] = (0 until a.length()).map { i ->
                val o = a.getJSONObject(i)
                Card(o.getString("code"), o.getString("simplified"), o.optString("traditional"),
                    o.optString("pinyin"), o.optString("english"), o.optBoolean("mastered"))
            }
        }
        return decks
    }
}
