package com.tema.ia.ui

import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class LoaderTest {
    private fun TestScope.loader(interval: Long = 1000, load: suspend () -> Int) = Loader(backgroundScope, interval, load)

    @Test fun `loads at start and keeps polling`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader { ++n }
        assertTrue(l.state.value is Load.Loading)
        l.start()
        testScheduler.runCurrent()
        assertEquals(1, (l.state.value as Load.Ready).value)
        advanceTimeBy(1001)
        assertEquals(2, (l.state.value as Load.Ready).value)
        advanceTimeBy(1000)
        assertEquals(3, (l.state.value as Load.Ready).value)
    }

    @Test fun `first failure is Failed`() = runTest(StandardTestDispatcher()) {
        val l = loader { throw ApiException(ApiError.NoServer, "x") }
        l.start(); testScheduler.runCurrent()
        assertTrue(l.state.value is Load.Failed)
    }

    @Test fun `a later failure keeps the old data and carries the error`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader { if (++n == 1) 7 else throw ApiException(ApiError.NoServer, "x") }
        l.start(); testScheduler.runCurrent()
        advanceTimeBy(1001)
        val s = l.state.value as Load.Ready
        assertEquals(7, s.value)
        assertTrue(s.error.isOffline)
    }

    @Test fun `recovery clears the error`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader { if (++n == 2) throw ApiException(ApiError.NoServer, "x") else n }
        l.start(); testScheduler.runCurrent()
        advanceTimeBy(1001)
        assertTrue((l.state.value as Load.Ready).error != null)
        advanceTimeBy(1000)
        assertEquals(null, (l.state.value as Load.Ready).error)
    }

    @Test fun `refresh loads right away`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader(60_000) { ++n }
        l.start(); testScheduler.runCurrent()
        l.refresh(); testScheduler.runCurrent()
        assertEquals(2, n)
    }

    @Test fun `stop ends polling and start resumes it`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader { ++n }
        l.start(); testScheduler.runCurrent()
        l.stop()
        advanceTimeBy(10_000)
        assertEquals(1, n)
        l.start(); testScheduler.runCurrent()
        assertEquals(2, n)
    }

    @Test fun `zero interval loads only on demand`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader(0) { ++n }
        l.start(); testScheduler.runCurrent()
        advanceTimeBy(100_000)
        assertEquals(1, n)
        l.refresh(); testScheduler.runCurrent()
        assertEquals(2, n)
    }

    @Test fun `loading flag is true only while a request runs`() = runTest(StandardTestDispatcher()) {
        val l = loader { kotlinx.coroutines.delay(100); 1 }
        l.start(); testScheduler.runCurrent()
        assertTrue(l.loading.value)
        advanceTimeBy(101)
        assertEquals(false, l.loading.value)
    }

    @Test fun `set replaces the data without a request`() = runTest(StandardTestDispatcher()) {
        val l = loader { 1 }
        l.set(42)
        assertEquals(42, (l.state.value as Load.Ready).value)
    }

    @Test fun `a refresh after the screen went away loads once and does not start polling in the background`() = runTest(StandardTestDispatcher()) {
        var n = 0
        val l = loader(5_000) { ++n }
        l.start(); testScheduler.runCurrent()      // the screen is visible
        l.stop()                                    // the screen went away (PollWhileVisible -> onStopOrDispose)
        l.refresh()                                 // an action finishes later and refreshes
        testScheduler.runCurrent()
        assertEquals(2, n)                          // the action's own fresh look
        advanceTimeBy(60_000)                       // a minute with the screen still away
        assertEquals("nothing is polled while the screen is away", 2, n)
        l.start(); testScheduler.runCurrent()       // the screen is back: the rhythm resumes
        assertEquals(3, n)
    }

    @Test fun `a refused password stops the rhythm until the owner refreshes`() = runTest(StandardTestDispatcher()) {
        var n = 0
        var kind = ApiError.BadToken
        val l = loader { n++; throw ApiException(kind, "x") }
        l.start(); testScheduler.runCurrent()
        advanceTimeBy(30_000)
        assertEquals("a wrong password is not sent every second (it would lock the address again)", 1, n)
        kind = ApiError.NoServer
        l.refresh(); testScheduler.runCurrent()
        assertEquals(2, n)
        advanceTimeBy(1_001)
        assertEquals("no connection keeps the rhythm: it comes back by itself", 3, n)
        l.stop()
    }
}
