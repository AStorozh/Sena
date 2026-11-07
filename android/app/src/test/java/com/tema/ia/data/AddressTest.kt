package com.tema.ia.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AddressTest {
    private fun ok(input: String) = (Address.parse(input) as Address.Result.Ok).baseUrl

    @Test fun `bare ip gets http and the default port`() = assertEquals("http://192.168.1.20:8765", ok("192.168.1.20"))
    @Test fun `explicit port is kept`() = assertEquals("http://192.168.1.20:9000", ok("192.168.1.20:9000"))
    @Test fun `local name works`() = assertEquals("http://mac.local:8765", ok("mac.local"))
    @Test fun `full url is trimmed to host and port`() = assertEquals("http://10.0.0.5:8765", ok("  http://10.0.0.5/some/path?x=1  "))
    @Test fun `emulator address works`() = assertEquals("http://10.0.2.2:8765", ok("10.0.2.2"))
    @Test fun `tailscale range works over http`() = assertEquals("http://100.101.102.103:8765", ok("100.101.102.103"))
    @Test fun `single label host works`() = assertEquals("http://macbook:8765", ok("macbook"))
    @Test fun `https without port keeps the standard port`() = assertEquals("https://sena.example.com", ok("https://sena.example.com"))
    @Test fun `https with port keeps it`() = assertEquals("https://sena.example.com:8443", ok("https://sena.example.com:8443/"))

    @Test fun `empty input`() = assertEquals(Address.Result.Empty, Address.parse("   "))
    @Test fun `public address over http needs https`() {
        assertEquals(Address.Result.NeedsHttps, Address.parse("sena.example.com"))
        assertEquals(Address.Result.NeedsHttps, Address.parse("http://8.8.8.8:8765"))
        assertEquals(Address.Result.NeedsHttps, Address.parse("172.32.0.1"))   // outside 172.16–31
        assertEquals(Address.Result.NeedsHttps, Address.parse("100.128.0.1"))  // outside the Tailscale range
    }
    @Test fun `garbage is invalid`() {
        assertEquals(Address.Result.Invalid, Address.parse("http://"))
        assertEquals(Address.Result.Invalid, Address.parse("ht!tp://[bad"))
    }

    @Test fun `private ranges`() {
        listOf("10.1.2.3", "192.168.0.1", "172.16.0.1", "172.31.255.255", "127.0.0.1", "169.254.1.1", "localhost", "foo.local", "x.ts.net").forEach { assertTrue(it, Address.isPrivateHost(it)) }
        listOf("1.1.1.1", "example.com", "172.15.0.1", "11.0.0.1", "192.169.0.1").forEach { assertFalse(it, Address.isPrivateHost(it)) }
    }
}
