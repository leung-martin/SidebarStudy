package com.sidebarstudy.wear

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The watch's YoYo sync against a fake YoYo, like mobile/test/yoyo.test.mjs. */
class YoyoSyncTest {
    private class FakeYoyo(val evil: Boolean = false, var expireOnce: Boolean = false) {
        val levels = linkedMapOf("A" to 2, "B" to -1, "C" to 5, "D" to 0, "E" to 4)
        val submits = mutableListOf<List<Pair<String, String>>>()
        var logins = 0

        fun page(codes: List<String>, masteryType: String): JSONObject {
            val list = codes.filter { masteryType == "all" || (levels.getValue(it) >= 5) == (masteryType == "mastered") }.shuffled()
            return JSONObject().put("totalFlashcards", list.size).put("flashcards", JSONArray().also { a ->
                list.take(50).forEach { a.put(JSONObject().put("_id", "id$it").put("code", it).put("levelId", "L1").put("masteryLevel", levels[it])) }
            })
        }

        private fun opt(value: String, key: String, parent: String) = JSONObject().put("value", value).put(key, parent)
        private val filterOptions = JSONObject()
            .put("courseOptions", JSONArray())
            .put("levelOptions", JSONArray().put(opt("L1", "courseId", "C1")))
            .put("unitOptions", JSONArray().put(opt("U1", "levelId", "L1")))
            .put("lessonOptions", JSONArray().put(opt("l1", "unitId", "U1")).put(opt("l2", "unitId", "U1")))

        val transport: Transport = { path, body, session ->
            when {
                path == "/auth/login" -> {
                    logins++
                    if (body.getString("password") == "pw") Resp(200, JSONObject().put("token", "plain"), "token=tok$logins; Path=/; HttpOnly")
                    else Resp(401, JSONObject(), "")
                }
                expireOnce -> { expireOnce = false; Resp(401, JSONObject(), "") }
                session?.startsWith("tok") != true -> Resp(401, JSONObject(), "")
                path == "/page/flashcards-mastery-manage_page" -> Resp(200, JSONObject().put("pageData", JSONObject().put("filterOptions", filterOptions)), "")
                path == "/flashcards/manage/cards" -> {
                    val f = body.getJSONObject("filters")
                    Resp(200, page(if (f.getString("lessonId") == "l1") listOf("A", "B") else listOf("C", "D", "E"), f.getString("masteryType")), "")
                }
                path == "/results/flashcard-mastery" -> {
                    val r = body.getJSONArray("responses")
                    val sent = (0 until r.length()).map { r.getJSONObject(it).let { o -> o.getString("flashcardId").removePrefix("id") to o.getString("answer") } }
                    submits += sent
                    for ((code, answer) in sent) levels[code] = nextLevel(levels.getValue(code), answer)
                    if (evil) levels["E"] = 0
                    Resp(200, JSONObject(), "")
                }
                path == "/me/provider" -> Resp(200, JSONObject().put("meData", JSONObject().put("isValid", true).put("masteredFlashcards", 1)), "")
                else -> Resp(404, JSONObject(), "")
            }
        }
    }

    private class MemStorage : SyncStorage {
        override var token = ""
        val files = HashMap<String, JSONObject>()
        override fun read(name: String) = files[name]
        override fun write(name: String, o: JSONObject) { files[name] = o }
    }

    private object NoEvents : YoyoSync.Events {
        override fun status(state: String) {}
        override fun levels(levels: Map<String, Int>, startedAt: Long, full: Boolean) {}
        override fun due(codes: List<String>, fetchedAt: Long) {}
    }

    private fun setup(fake: FakeYoyo): YoyoSync {
        val s = YoyoSync(MemStorage(), CoroutineScope(Dispatchers.Default), NoEvents, fake.transport)
        s.credentials = "me@x" to "pw"
        return s
    }

    @Test fun answersAreSent() = runBlocking {
        val fake = FakeYoyo()
        val s = setup(fake)
        s.runCycle()  // due list + first download
        s.record("A", "good"); s.record("C", "hard"); s.record("D", "easy")
        s.runCycle()
        assertEquals(listOf(listOf("A" to "good", "C" to "hard", "D" to "easy")), fake.submits)
        assertEquals(mapOf("A" to 3, "B" to -1, "C" to 0, "D" to 2, "E" to 4), fake.levels.toMap())
        assertEquals("", s.blocked)
        assertEquals(0, s.pendingCount)
    }

    @Test fun guardStopsSendingWhenAnUnansweredCardDrops() = runBlocking {
        val fake = FakeYoyo(evil = true)
        val s = setup(fake)
        s.runCycle()
        s.record("A", "good")
        s.runCycle()
        assertTrue(s.blocked, s.blocked.contains("E"))
        s.record("D", "easy")
        s.runCycle()
        assertEquals(1, fake.submits.size)   // nothing more was sent
        assertEquals(1, s.pendingCount)      // D is kept
    }

    @Test fun masterSendsGoodThreeTimesInSeparateUploads() = runBlocking {
        val fake = FakeYoyo()
        val s = setup(fake)
        s.runCycle()
        repeat(3) { s.record("B", "easy") }
        s.record("A", "good")
        s.runCycle()
        assertEquals(listOf(listOf("B" to "easy", "A" to "good"), listOf("B" to "easy"), listOf("B" to "easy")), fake.submits)
        assertEquals(MASTERED, fake.levels["B"])
        assertEquals("", s.blocked)
    }

    @Test fun expiredSessionSignsInAgain() = runBlocking {
        val fake = FakeYoyo()
        val s = setup(fake)
        s.runCycle()
        fake.expireOnce = true
        s.record("D", "easy")
        s.runCycle()
        assertEquals(2, fake.logins)
        assertEquals(1, fake.submits.size)
    }
}
