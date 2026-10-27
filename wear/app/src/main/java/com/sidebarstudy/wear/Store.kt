package com.sidebarstudy.wear

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.json.JSONObject
import java.io.File

/** Your level and answer counts for one card. level: -1 new, 0-4 learning, 5 mastered (YoYo's scale). */
data class Progress(var level: Int = -1, var ok: Int = 0, var fail: Int = 0, var last: Long = 0)

/** Small JSON files in the app's private storage, plus the YoYo password in encrypted prefs. */
class Store(private val context: Context) : SyncStorage {
    val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    private val secure by lazy {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(context, "secure", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
    }

    var password: String?
        get() = runCatching { secure.getString("yoyo_password", null) }.getOrNull()
        set(value) { secure.edit().apply { if (value == null) remove("yoyo_password") else putString("yoyo_password", value) }.apply() }

    override var token: String
        get() = prefs.getString("yoyo_token", "") ?: ""
        set(value) { prefs.edit().putString("yoyo_token", value).apply() }

    override fun read(name: String) = readJson(name)
    override fun write(name: String, o: JSONObject) = writeJson(name, o)

    fun readJson(name: String): JSONObject? = runCatching { JSONObject(File(context.filesDir, name).readText()) }.getOrNull()

    fun writeJson(name: String, o: JSONObject) {
        val tmp = File(context.filesDir, "$name.tmp")
        tmp.writeText(o.toString())
        tmp.renameTo(File(context.filesDir, name))
    }

    fun loadProgress(): MutableMap<String, Progress> {
        val o = readJson("progress.json") ?: return mutableMapOf()
        return o.keys().asSequence().associateWith { k ->
            o.getJSONObject(k).let { Progress(it.optInt("level", -1), it.optInt("ok"), it.optInt("fail"), it.optLong("last")) }
        }.toMutableMap()
    }

    fun saveProgress(progress: Map<String, Progress>) {
        val o = JSONObject()
        for ((k, p) in progress) o.put(k, JSONObject().put("level", p.level).put("ok", p.ok).put("fail", p.fail).put("last", p.last))
        writeJson("progress.json", o)
    }
}
