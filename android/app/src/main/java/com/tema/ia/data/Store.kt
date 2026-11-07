package com.tema.ia.data

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

data class Connection(val baseUrl: String, val token: String)

enum class ThemeMode { System, Dark, Light }

/** What a typed lock code got: right, wrong, or "wait [ms] before the next try" (too many wrong ones in a row). */
sealed interface PinCheck {
    data object Right : PinCheck
    data object Wrong : PinCheck
    data class Wait(val ms: Long) : PinCheck
}

/** What the app remembers: the Mac's address, the password, the theme, demo mode. Stored in the app's private folder, excluded from backups. */
class Store(private val data: DataStore<Preferences>) {
    private val addressKey = stringPreferencesKey("address")
    private val tokenKey = stringPreferencesKey("token")
    private val themeKey = stringPreferencesKey("theme")
    private val demoKey = booleanPreferencesKey("demo")
    private val pinHashKey = stringPreferencesKey("pin_hash")
    private val pinSaltKey = stringPreferencesKey("pin_salt")
    private val pinFailsKey = intPreferencesKey("pin_fails")          // wrong codes in a row (kept across restarts: killing the app doesn't reset it)
    private val pinWaitKey = longPreferencesKey("pin_wait_until")     // no code is checked before this moment
    private val biometricKey = booleanPreferencesKey("biometric_enabled")

    val connection: Flow<Connection?> = data.data.map { p ->
        val address = p[addressKey]
        val token = p[tokenKey]
        if (address.isNullOrBlank() || token.isNullOrBlank()) null else Connection(address, token)
    }

    val demo: Flow<Boolean> = data.data.map { it[demoKey] ?: false }

    val theme: Flow<ThemeMode> = data.data.map { p -> ThemeMode.entries.firstOrNull { it.name == p[themeKey] } ?: ThemeMode.System }

    /** Unlock with a fingerprint or face instead of typing the code — off until the owner turns it on in Settings, and only ever a shortcut for the same PIN, never a replacement for it. */
    val biometricEnabled: Flow<Boolean> = data.data.map { it[biometricKey] ?: false }

    suspend fun setBiometricEnabled(on: Boolean) {
        data.edit { it[biometricKey] = on }
    }

    suspend fun saveConnection(connection: Connection) {
        data.edit { it[addressKey] = connection.baseUrl; it[tokenKey] = connection.token; it[demoKey] = false }
    }

    suspend fun clearConnection() {
        data.edit { it.remove(addressKey); it.remove(tokenKey); it[demoKey] = false }
    }

    suspend fun setDemo(on: Boolean) {
        data.edit { it[demoKey] = on }
    }

    suspend fun setTheme(mode: ThemeMode) {
        data.edit { it[themeKey] = mode.name }
    }

    /** Whether the app's own lock code is set up; the gate asks to create one the first time, then asks to enter it every time after. */
    suspend fun hasPin(): Boolean = data.data.first()[pinHashKey] != null

    suspend fun setPin(pin: String) {
        val salt = UUID.randomUUID().toString()
        data.edit { it[pinSaltKey] = salt; it[pinHashKey] = hash(salt, pin); it.remove(pinFailsKey); it.remove(pinWaitKey) }
    }

    /**
     * Checks a typed code. Four digits are ten thousand codes: after [FREE_TRIES] wrong ones in a row each next try waits, longer
     * every time ([pinPause]), so guessing would take months. The right code — or a fingerprint ([clearPinTries]) — starts over.
     */
    suspend fun checkPin(pin: String, now: Long = System.currentTimeMillis()): PinCheck {
        val p = data.data.first()
        val waitUntil = p[pinWaitKey] ?: 0L
        if (waitUntil - now > MAX_PAUSE) {   // the phone's clock went back: the pause is counted from now, not from a moment ahead
            data.edit { it[pinWaitKey] = now + MAX_PAUSE }
            return PinCheck.Wait(MAX_PAUSE)
        }
        if (now < waitUntil) return PinCheck.Wait(waitUntil - now)
        val salt = p[pinSaltKey] ?: return PinCheck.Wrong
        val stored = p[pinHashKey] ?: return PinCheck.Wrong
        if (hash(salt, pin) == stored) {
            clearPinTries()
            return PinCheck.Right
        }
        val fails = (p[pinFailsKey] ?: 0) + 1
        val pause = pinPause(fails)
        data.edit { it[pinFailsKey] = fails; if (pause > 0) it[pinWaitKey] = now + pause }
        return if (pause > 0) PinCheck.Wait(pause) else PinCheck.Wrong
    }

    suspend fun clearPinTries() {
        data.edit { it.remove(pinFailsKey); it.remove(pinWaitKey) }
    }

    private fun hash(salt: String, pin: String): String =
        MessageDigest.getInstance("SHA-256").digest((salt + pin).toByteArray()).joinToString("") { "%02x".format(it) }

    companion object {
        const val FREE_TRIES = 5
        const val MAX_PAUSE = 15 * 60_000L

        /** 30 seconds after the fifth wrong code in a row, twice as long after each next one, at most 15 minutes. */
        fun pinPause(fails: Int): Long = if (fails < FREE_TRIES) 0L else minOf(30_000L shl minOf(fails - FREE_TRIES, 5), MAX_PAUSE)
    }
}
