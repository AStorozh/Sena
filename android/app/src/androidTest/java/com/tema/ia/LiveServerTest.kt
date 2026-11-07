package com.tema.ia

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.HttpSenaApi
import com.tema.ia.data.IntegrationCatalog
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Reading from the real Sena server (read-only: nothing is sent or changed).
 * Run with: -Pandroid.testInstrumentationRunnerArguments.sena_token=<APP_TOKEN> [.sena_address=http://10.0.2.2:8765]
 * Without a password, the checks are skipped.
 */
@RunWith(AndroidJUnit4::class)
class LiveServerTest {
    private val args = InstrumentationRegistry.getArguments()
    private val token = args.getString("sena_token")
    private val address = args.getString("sena_address") ?: "http://10.0.2.2:8765"
    private lateinit var api: HttpSenaApi

    @Before fun setUp() {
        assumeTrue("нет пароля сервера", !token.isNullOrBlank())
        api = HttpSenaApi(address, token!!)
    }

    @Test fun overview_is_readable() { val o = runBlocking { api.overview() }; assertTrue(o.spentToday >= 0.0) }

    @Test fun tasks_history_and_rules_load() {
        runBlocking {
            val t = api.tasks()
            assertTrue(t.finished.all { it.id.isNotBlank() && it.text.isNotBlank() })
            api.history()
            api.rules()
        }
    }

    @Test fun memory_people_notes_skills_and_search_load() {
        runBlocking {
            val facts = api.facts()
            assertTrue("в памяти есть разделы", facts.isNotEmpty())
            api.people()
            val notes = api.notes()
            if (notes.isNotEmpty()) assertTrue(api.noteText(notes.first().path).isNotBlank())
            val skills = api.skills()
            if (skills.isNotEmpty()) assertTrue(api.skill(skills.first().name).recipe.isNotBlank())
            api.search("Сена")
        }
    }

    @Test fun every_catalog_integration_exists_on_the_server() {
        val live = runBlocking { api.integrations() }
        assertEquals(IntegrationCatalog.all.map { it.id }.toSet(), live.keys)
    }

    @Test fun config_has_the_settings_the_app_shows() {
        val c = runBlocking { api.config() }
        assertTrue(c.verbosityOptions.isNotEmpty() && c.effortOptions.isNotEmpty())
        assertTrue(c.budgetMax > 0)
    }

    @Test fun wrong_password_is_recognised() {
        val e = try { runBlocking { HttpSenaApi(address, "definitely-wrong").overview() }; null } catch (e: ApiException) { e }
        assertEquals(ApiError.BadToken, e?.kind)
    }
}
