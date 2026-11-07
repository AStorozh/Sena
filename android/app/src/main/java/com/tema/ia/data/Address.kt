package com.tema.ia.data

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** The Mac's address, as a person would type it: `mac.local`, `192.168.1.20`, `http://192.168.1.20:8765`, `https://…`. */
object Address {
    const val DEFAULT_PORT = 8765
    const val VOICE_PORT = 8767   // the live voice beside it (sena/config.py VOICE_PORT)

    sealed interface Result {
        data class Ok(val baseUrl: String) : Result
        data object Empty : Result
        data object Invalid : Result
        /** Plain http is only allowed on your own network; everything else needs https. */
        data object NeedsHttps : Result
    }

    fun parse(input: String): Result {
        val text = input.trim()
        if (text.isEmpty()) return Result.Empty
        val withScheme = if ("://" in text) text else "http://$text"
        val url: HttpUrl = withScheme.toHttpUrlOrNull() ?: return Result.Invalid
        if (url.host.isBlank()) return Result.Invalid
        if (!url.isHttps && !isPrivateHost(url.host)) return Result.NeedsHttps
        // Default port 8765 applies only to http; https without a port stays on the usual 443
        val explicitPort = Regex("^[a-z]+://[^/]*:(\\d+)(/|$)", RegexOption.IGNORE_CASE).containsMatchIn(withScheme)
        val port = if (!url.isHttps && !explicitPort) DEFAULT_PORT else url.port
        val base = url.newBuilder().port(port).encodedPath("/").query(null).fragment(null).build()
        return Result.Ok(base.toString().trimEnd('/'))
    }

    /** A local address: localhost, `.local`, the emulator address, private networks, Tailscale. */
    fun isPrivateHost(rawHost: String): Boolean {
        val host = rawHost.lowercase().trim('[', ']')
        if (host == "localhost" || host == "::1" || host == "10.0.2.2") return true
        if (host.endsWith(".local") || host.endsWith(".ts.net") || host.endsWith(".lan") || host.endsWith(".home.arpa")) return true
        if (!host.contains('.') && !host.contains(':')) return true   // a name without a dot: "macbook"
        if (host.startsWith("fc") || host.startsWith("fd") || host.startsWith("fe80")) return host.contains(':')
        val p = host.split('.').mapNotNull { it.toIntOrNull() }
        if (p.size != 4 || p.any { it !in 0..255 }) return false
        return p[0] == 10 || p[0] == 127 ||
            (p[0] == 192 && p[1] == 168) ||
            (p[0] == 172 && p[1] in 16..31) ||
            (p[0] == 100 && p[1] in 64..127) ||   // Tailscale
            (p[0] == 169 && p[1] == 254)
    }
}
