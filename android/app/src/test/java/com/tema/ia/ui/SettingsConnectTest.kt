package com.tema.ia.ui

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.SavedStateHandle
import com.tema.ia.AppContainer
import com.tema.ia.MainDispatcherRule
import com.tema.ia.R
import com.tema.ia.Session
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.Connection
import com.tema.ia.data.DemoApi
import com.tema.ia.data.PinCheck
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.Store
import com.tema.ia.data.ThemeMode
import com.tema.ia.ui.screens.connect.CheckState
import com.tema.ia.ui.screens.connect.ConnectViewModel
import com.tema.ia.ui.screens.settings.SettingsViewModel
import com.tema.ia.ui.screens.settings.budgetOf
import com.tema.ia.viewModelOf
import com.tema.ia.watch
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsConnectTest {
    @get:Rule val main = MainDispatcherRule()
    @get:Rule val tmp = TemporaryFolder()

    private fun TestScope.store() = Store(PreferenceDataStoreFactory.create(scope = backgroundScope) { File(tmp.root, "p${System.nanoTime()}.preferences_pb") })

    // ---------- store and session ----------

    @Test fun `store keeps connection theme and demo`() = runTest {
        val s = store()
        assertNull(s.connection.first())
        assertEquals(ThemeMode.System, s.theme.first())
        s.saveConnection(Connection("http://10.0.2.2:8765", "pw"))
        assertEquals(Connection("http://10.0.2.2:8765", "pw"), s.connection.first())
        s.setTheme(ThemeMode.Dark)
        assertEquals(ThemeMode.Dark, s.theme.first())
        s.setDemo(true)
        assertTrue(s.demo.first())
        s.clearConnection()
        assertNull(s.connection.first())
        assertFalse(s.demo.first())
        assertEquals(ThemeMode.Dark, s.theme.first())      // theme stays
    }

    @Test fun `half saved connection counts as none`() = runTest {
        val s = store()
        s.saveConnection(Connection("", "pw"))
        assertNull(s.connection.first())
    }

    @Test fun `session follows the store and does not restart on a theme change`() = runTest {
        val s = store()
        var made = 0
        val container = AppContainer(s, backgroundScope, demoFactory = { DemoApi(latencyMs = 0) }, httpFactory = { made++; ScriptedApi() })
        watch(container.session)
        runCurrent()
        assertEquals(Session.NeedsConnection, container.session.value)
        s.saveConnection(Connection("http://10.0.2.2:8765", "pw")); runCurrent()
        val first = container.session.value as Session.Active
        assertFalse(first.demo)
        assertEquals("http://10.0.2.2:8765", first.address)
        s.setTheme(ThemeMode.Light); runCurrent()
        assertEquals(first, container.session.value)      // changing the theme does not recreate the connection
        assertEquals(1, made)
        s.setDemo(true); runCurrent()
        assertTrue((container.session.value as Session.Active).demo)
        s.clearConnection(); runCurrent()
        assertEquals(Session.NeedsConnection, container.session.value)
    }

    // ---------- connection ----------

    private fun TestScope.connect(check: suspend (Connection) -> Unit = {}, s: Store = store(), initial: String = "") =
        viewModelOf { ConnectViewModel(SavedStateHandle(), s, check, initial) } to s

    @Test fun `validation gives readable errors and does not call the server`() = runTest {
        var calls = 0
        val (vm, _) = connect({ calls++ })
        vm.submit()
        assertEquals(UiText.Res(R.string.connect_empty_address), vm.ui.value.addressError)
        vm.onAddress("sena.example.com"); vm.submit()
        assertEquals(UiText.Res(R.string.err_cleartext), vm.ui.value.addressError)
        vm.onAddress("192.168.1.20"); vm.submit()
        assertNull(vm.ui.value.addressError)
        assertEquals(UiText.Res(R.string.connect_empty_token), vm.ui.value.tokenError)
        assertEquals(0, calls)
    }

    @Test fun `successful check saves the normalized connection`() = runTest {
        var seen: Connection? = null
        val (vm, s) = connect({ seen = it })
        vm.onAddress("192.168.1.20"); vm.onToken("  pass  "); vm.submit(); runCurrent()
        assertEquals(Connection("http://192.168.1.20:8765", "pass"), seen)
        assertEquals(seen, s.connection.first())
    }

    @Test fun `wrong password is marked on the password field and nothing is saved`() = runTest {
        val (vm, s) = connect({ throw ApiException(ApiError.BadToken, "x") })
        vm.onAddress("10.0.2.2"); vm.onToken("bad"); vm.submit(); runCurrent()
        assertEquals(UiText.Res(R.string.err_bad_token), vm.ui.value.tokenError)
        assertTrue(vm.ui.value.check is CheckState.Failed)
        assertNull(s.connection.first())
    }

    @Test fun `unreachable server is explained`() = runTest {
        val (vm, s) = connect({ throw ApiException(ApiError.NoServer, "x") })
        vm.onAddress("10.0.2.2"); vm.onToken("pw"); vm.submit(); runCurrent()
        assertEquals(CheckState.Failed(UiText.Res(R.string.err_no_server)), vm.ui.value.check)
        assertNull(vm.ui.value.tokenError)
        assertNull(s.connection.first())
    }

    @Test fun `editing clears the error and the initial address is used`() = runTest {
        val (vm, _) = connect({ throw ApiException(ApiError.NoServer, "x") }, initial = "http://10.0.2.2:8765")
        assertEquals("http://10.0.2.2:8765", vm.ui.value.address)
        vm.onToken("pw"); vm.submit(); runCurrent()
        vm.onToken("pw2")
        assertEquals(CheckState.Idle, vm.ui.value.check)
    }

    @Test fun `demo mode is stored`() = runTest {
        val (vm, s) = connect()
        vm.demo(); runCurrent()
        assertTrue(s.demo.first())
    }

    @Test fun `the address survives process death, the password is never put in saved state`() = runTest {
        val handle = SavedStateHandle()
        val s = store()
        ConnectViewModel(handle, s, {}).also { it.onAddress("10.0.2.2"); it.onToken("pw") }
        val again = ConnectViewModel(handle, s, {})
        assertEquals("10.0.2.2", again.ui.value.address)
        assertEquals("", again.ui.value.token)
        assertFalse("Н-4: the password lives only in the protected store", handle.keys().any { handle.get<Any>(it) == "pw" })
    }

    // ---------- the lock code ----------

    @Test fun `the right code opens, a wrong one doesn't`() = runTest {
        val s = store()
        s.setPin("1234")
        assertEquals(PinCheck.Right, s.checkPin("1234", now = 0))
        assertEquals(PinCheck.Wrong, s.checkPin("0000", now = 0))
    }

    @Test fun `after five wrong codes each next try waits, longer every time, and survives a restart`() = runTest {
        val s = store()
        s.setPin("1234")
        var now = 1_000_000L
        repeat(Store.FREE_TRIES - 1) { assertEquals(PinCheck.Wrong, s.checkPin("0000", now)) }
        assertEquals(PinCheck.Wait(30_000), s.checkPin("0000", now))
        assertEquals("even the right code waits", PinCheck.Wait(30_000), s.checkPin("1234", now))
        now += 30_000
        assertEquals(PinCheck.Wait(60_000), s.checkPin("0000", now))   // the sixth wrong one doubles the pause
        now += 60_000
        assertEquals(PinCheck.Right, s.checkPin("1234", now))
        assertEquals("the right code starts over", PinCheck.Wrong, s.checkPin("0000", now))
        assertEquals(Store.MAX_PAUSE, Store.pinPause(40))
    }

    @Test fun `a clock moved back can't lock the owner out for longer than the longest pause`() = runTest {
        val s = store()
        s.setPin("1234")
        val now = 10_000_000L
        repeat(Store.FREE_TRIES) { s.checkPin("0000", now) }
        assertEquals(PinCheck.Wait(Store.MAX_PAUSE), s.checkPin("1234", now - 24 * 3_600_000L))   // the phone's clock went a day back
        assertEquals(PinCheck.Right, s.checkPin("1234", now - 24 * 3_600_000L + Store.MAX_PAUSE))
    }

    @Test fun `a fingerprint unlock starts the count over`() = runTest {
        val s = store()
        s.setPin("1234")
        repeat(Store.FREE_TRIES - 1) { s.checkPin("0000", 0) }
        s.clearPinTries()
        repeat(Store.FREE_TRIES - 1) { assertEquals(PinCheck.Wrong, s.checkPin("0000", 0)) }
    }

    @Test fun `going to the background locks, a rotation or a theme change doesn't`() {
        assertTrue(closesLock(Lifecycle.Event.ON_STOP, changingConfigurations = false))
        assertFalse(closesLock(Lifecycle.Event.ON_STOP, changingConfigurations = true))
        assertFalse(closesLock(Lifecycle.Event.ON_PAUSE, changingConfigurations = false))
    }

    // ---------- settings ----------

    private fun TestScope.settings(api: ScriptedApi, s: Store = store()) = viewModelOf { SettingsViewModel(api, s) }.also { it.loader.start() }

    @Test fun `a setting is shown at once and confirmed by the server answer`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = settings(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.change("verbosity", "medium"); runCurrent()
        assertEquals("medium", vm.loader.state.value.valueOrNull?.settings?.verbosity)
        assertEquals(listOf<UiText>(UiText.Res(R.string.settings_saved)), seen)
        vm.change("heartbeat", false)
        assertFalse(vm.loader.state.value.valueOrNull!!.settings.heartbeat)
        vm.change("budget_usd_day", 7)
        assertEquals(7.0, vm.loader.state.value.valueOrNull!!.settings.budgetUsdDay, 0.0)
        vm.change("budget_usd_day", 2.5)
        assertEquals("cents are kept, not cut to whole dollars", 2.5, vm.loader.state.value.valueOrNull!!.settings.budgetUsdDay, 0.0)
    }

    @Test fun `the budget is read as typed, with a dot or a comma`() {
        assertEquals(2.5, budgetOf("2,5")!!, 0.0)
        assertEquals(2.5, budgetOf(" 2.5 ")!!, 0.0)
        assertEquals(0.0, budgetOf("0")!!, 0.0)
        assertNull(budgetOf("2.5.1"))
        assertNull(budgetOf(""))
    }

    @Test fun `server failure restores the previous value`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = settings(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        api.failures["updateSettings"] = ApiException(ApiError.NoServer, "x")
        vm.change("tidy", false); runCurrent()
        assertTrue(vm.loader.state.value.valueOrNull!!.settings.tidy)
        assertTrue(seen.single() is UiText.Res)
    }

    @Test fun `without data nothing is changed`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["config"] = ApiException(ApiError.NoServer, "x") }
        val vm = settings(api)
        vm.change("tidy", false)
        assertEquals(0, api.count("updateSettings"))
    }

    @Test fun `theme and disconnect`() = runTest {
        val s = store()
        val vm = settings(ScriptedApi(), s)
        watch(vm.theme)
        vm.setTheme(ThemeMode.Light); runCurrent()
        assertEquals(ThemeMode.Light, vm.theme.value)
        s.saveConnection(Connection("http://10.0.2.2:8765", "pw"))
        vm.askDisconnect()
        assertTrue(vm.confirmDisconnect.value)
        vm.disconnect(); runCurrent()
        assertNull(s.connection.first())
        assertFalse(vm.confirmDisconnect.value)
    }
}
