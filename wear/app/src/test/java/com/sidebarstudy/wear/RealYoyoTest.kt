package com.sidebarstudy.wear

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The real YoYo, READ-ONLY, signed in with the PC app's saved session (../yoyo_auth.json):
 * one sync cycle with nothing to send, so it only reads the due list and every card's level.
 * Runs only with YOYO_REAL=1.
 */
class RealYoyoTest {
    @Test fun readsDueListAndLevels() = runBlocking {
        assumeTrue(System.getenv("YOYO_REAL") == "1")
        val auth = File("../../yoyo_auth.json").takeIf { it.exists() } ?: File("../yoyo_auth.json")
        val cookie = JSONObject(auth.readText()).getString("cookie")
        val storage = object : SyncStorage {
            override var token = cookie.split(";").map { it.trim() }.first { it.startsWith("token=") }.removePrefix("token=")
            override fun read(name: String): JSONObject? = null
            override fun write(name: String, o: JSONObject) {}
        }
        val states = mutableListOf<String>()
        var due = -1
        var seen = emptyMap<String, Int>()
        val events = object : YoyoSync.Events {
            override fun status(state: String) { states += state }
            override fun levels(levels: Map<String, Int>, startedAt: Long, full: Boolean) { if (full) seen = levels }
            override fun due(codes: List<String>, fetchedAt: Long) { due = codes.size }
        }
        val s = YoyoSync(storage, CoroutineScope(Dispatchers.Default), events)
        val t = System.currentTimeMillis()
        s.runCycle()
        val mastered = seen.values.count { it >= MASTERED }
        println("real YoYo: states $states | due $due | ${seen.size} cards, $mastered mastered | ${System.currentTimeMillis() - t} ms")
        assertEquals("up to date", states.last())
        assertTrue(seen.size > 10_000)
        assertEquals(0, s.pendingCount)
    }
}
