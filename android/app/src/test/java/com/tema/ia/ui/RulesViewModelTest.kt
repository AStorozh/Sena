package com.tema.ia.ui

import com.tema.ia.MainDispatcherRule
import com.tema.ia.R
import com.tema.ia.viewModelOf
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.DemoApi
import com.tema.ia.data.RuleKind
import com.tema.ia.data.ScriptedApi
import com.tema.ia.ui.screens.rules.RulesViewModel
import com.tema.ia.watch
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class RulesViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private fun kotlinx.coroutines.test.TestScope.rules(api: ScriptedApi) = viewModelOf { RulesViewModel(api) }.also { it.start() }
    private fun RulesViewModel.start() = loader.start()
    private fun RulesViewModel.list() = loader.state.value.valueOrNull.orEmpty()

    @Test fun `list loads all kinds`() = runTest {
        val vm = rules(ScriptedApi(DemoApi(latencyMs = 0)))
        assertEquals(setOf(RuleKind.Rule, RuleKind.Job, RuleKind.Alert), vm.list().map { it.kind }.toSet())
    }

    @Test fun `toggle changes the row at once and tells the server`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = rules(api)
        val rule = vm.list().first { it.id == "r1" }
        vm.toggle(rule, false)
        assertFalse(vm.list().first { it.id == "r1" }.enabled)
        assertEquals(1, api.count("setRuleEnabled"))
    }

    @Test fun `a refused toggle comes back with a message`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val seen = mutableListOf<UiText>()
        val vm = rules(api)
        watch(vm.messages) { seen += it }
        val rule = vm.list().first { it.id == "r1" }
        api.failures["setRuleEnabled"] = ApiException(ApiError.Server, "Такого правила нет")
        vm.toggle(rule, false)
        runCurrent()
        assertTrue(vm.list().first { it.id == "r1" }.enabled)     // reverted, matching the server
        assertEquals(listOf<UiText>(UiText.Raw("Такого правила нет")), seen)
    }

    @Test fun `add ignores blank text and reports success`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val seen = mutableListOf<UiText>()
        val vm = rules(api)
        watch(vm.messages) { seen += it }
        vm.add("   ")
        assertEquals(0, api.count("addRule"))
        vm.openAdd()
        vm.add(" Каждое утро погода ")
        runCurrent()
        assertEquals(1, api.count("addRule"))
        assertFalse(vm.adding.value)
        assertEquals(listOf<UiText>(UiText.Res(R.string.rules_added)), seen)
        assertEquals("Каждое утро погода", vm.list().first().text)
    }

    @Test fun `add refused keeps the sheet open`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["addRule"] = ApiException(ApiError.Server, "Слишком коротко") }
        val vm = rules(api)
        vm.openAdd(); vm.add("ab")
        assertTrue(vm.adding.value)
        assertFalse(vm.saving.value)
    }

    @Test fun `delete asks first, then removes`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = rules(api)
        val rule = vm.list().first { it.id == "j1" }
        vm.askDelete(rule)
        assertNotNull(vm.deleting.value)
        vm.dismissDelete()
        assertEquals(0, api.count("removeRule"))
        vm.askDelete(rule); vm.confirmDelete()
        runCurrent()
        assertEquals(1, api.count("removeRule"))
        assertTrue(vm.list().none { it.id == "j1" })
    }
}
