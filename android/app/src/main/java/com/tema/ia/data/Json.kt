package com.tema.ia.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

// The Sena server returns JSON where fields can be empty, extra, or of a different type (e.g. null instead of a string).
// The app reads the response tolerantly: whatever is missing or the wrong type falls back to a default instead of crashing the screen.

val SenaJson = Json { ignoreUnknownKeys = true }

/** The server always answers with an object or a list; anything else (a proxy error page, an empty string) counts as an unreadable answer. */
fun parseJson(text: String): JsonElement {
    val parsed = try {
        SenaJson.parseToJsonElement(text)
    } catch (e: Exception) {
        throw ApiException(ApiError.BadAnswer, "Сервер ответил непонятно")
    }
    if (parsed !is JsonObject && parsed !is JsonArray) throw ApiException(ApiError.BadAnswer, "Сервер ответил непонятно")
    return parsed
}

fun JsonElement?.obj(): JsonObject = this as? JsonObject ?: JsonObject(emptyMap())
fun JsonElement?.arr(): JsonArray = this as? JsonArray ?: JsonArray(emptyList())
fun JsonObject.obj(key: String): JsonObject = this[key].obj()
fun JsonObject.arr(key: String): JsonArray = this[key].arr()

private fun JsonElement?.prim(): JsonPrimitive? = (this as? JsonPrimitive)?.takeIf { it !is JsonNull }

fun JsonObject.str(key: String, default: String = ""): String = this[key].prim()?.content ?: default
fun JsonObject.strOrNull(key: String): String? = this[key].prim()?.content?.takeIf { it.isNotBlank() }
fun JsonObject.bool(key: String, default: Boolean = false): Boolean = this[key].prim()?.let { it.booleanOrNull ?: default } ?: default
fun JsonObject.boolOrNull(key: String): Boolean? = this[key].prim()?.booleanOrNull
fun JsonObject.num(key: String, default: Double = 0.0): Double = this[key].prim()?.doubleOrNull ?: default
fun JsonObject.numOrNull(key: String): Double? = this[key].prim()?.doubleOrNull
fun JsonObject.int(key: String, default: Int = 0): Int = this[key].prim()?.let { it.longOrNull?.toInt() ?: it.doubleOrNull?.toInt() } ?: default
fun JsonObject.intOrNull(key: String): Int? = this[key].prim()?.let { it.longOrNull?.toInt() ?: it.doubleOrNull?.toInt() }

fun JsonArray.objects(): List<JsonObject> = mapNotNull { it as? JsonObject }
fun JsonArray.strings(): List<String> = mapNotNull { (it as? JsonPrimitive)?.takeIf { p -> p !is JsonNull }?.content }
