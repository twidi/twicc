package com.twidi.twicc

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class Instance(val id: String, val name: String, val address: String) {
    val profileName: String get() = "twicc-$id"
    val url: InstanceUrl get() = InstanceUrl.parse(address)
}

/** One small, private preferences file. Passwords remain in the server's login flow. */
class InstanceStore(context: Context) {
    private val preferences = context.getSharedPreferences("instances", Context.MODE_PRIVATE)

    fun all(): List<Instance> {
        val encoded = runCatching { JSONArray(preferences.getString("items", "[]")) }
            .getOrElse { return emptyList() }
        return (0 until encoded.length()).mapNotNull { index ->
            runCatching {
                val row = encoded.getJSONObject(index)
                val id = UUID.fromString(row.getString("id")).toString()
                Instance(id, row.getString("name"), InstanceUrl.parse(row.getString("address")).origin)
            }.getOrNull()
        }.distinctBy { it.id }
    }

    fun find(id: String?): Instance? = all().find { it.id == id }
    fun last(): Instance? = find(preferences.getString("last", null))

    fun select(instance: Instance) {
        preferences.edit().putString("last", instance.id).apply()
    }

    fun save(previous: Instance?, name: String, input: String): Instance {
        val address = InstanceUrl.parse(input).origin
        val instances = all()
        if (instances.any { it.id != previous?.id && it.address == address })
            throw InstanceValidationException(InstanceValidationCode.DUPLICATE)
        val instance = Instance(
            previous?.id ?: UUID.randomUUID().toString(),
            name.trim().ifEmpty { java.net.URI(address).host },
            address,
        )
        // A new origin gets a new profile. Do not silently reuse the old server's storage.
        val updated = if (previous != null && previous.address != address) {
            instance.copy(id = UUID.randomUUID().toString())
        } else instance
        write(instances.filterNot { it.id == previous?.id } + updated)
        if (previous != null && preferences.getString("last", null) == previous.id) select(updated)
        return updated
    }

    fun remove(instance: Instance) {
        write(all().filterNot { it.id == instance.id })
        if (preferences.getString("last", null) == instance.id) {
            preferences.edit().remove("last").apply()
        }
    }

    fun page(instance: Instance): String? = preferences.getString("page-${instance.id}", null)
        ?.takeIf { instance.url.allowsNavigation(it) }

    fun rememberPage(instance: Instance, url: String) {
        if (instance.url.allowsNavigation(url)) preferences.edit().putString("page-${instance.id}", url).apply()
    }

    fun deferProfileDeletion(instance: Instance) {
        val edit = preferences.edit().remove("page-${instance.id}")
        // Keep an independent tombstone until each build has requested its own storage cleanup.
        listOf("gecko", "webview").forEach { engine ->
            edit.putStringSet("removedProfiles-$engine", pendingProfileDeletions(engine) + instance.profileName)
        }
        edit.apply()
    }

    fun pendingProfileDeletions(engine: String): Set<String> =
        preferences.getStringSet("removedProfiles-$engine", emptySet()).orEmpty().toSet()

    fun profileDeleted(name: String, engine: String) {
        preferences.edit().putStringSet("removedProfiles-$engine", pendingProfileDeletions(engine) - name).apply()
    }

    private fun write(instances: List<Instance>) {
        val encoded = JSONArray()
        instances.forEach {
            encoded.put(JSONObject().put("id", it.id).put("name", it.name).put("address", it.address))
        }
        preferences.edit().putString("items", encoded.toString()).apply()
    }

}
