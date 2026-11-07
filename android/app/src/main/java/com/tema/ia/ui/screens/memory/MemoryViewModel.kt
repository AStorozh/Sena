package com.tema.ia.ui.screens.memory

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.INBOX_FOLDER
import com.tema.ia.data.NoteInfo
import com.tema.ia.data.Person
import com.tema.ia.data.SearchHit
import com.tema.ia.data.SearchKind
import com.tema.ia.data.SenaApi
import com.tema.ia.data.SkillDetail
import com.tema.ia.data.factLine
import com.tema.ia.data.noteParts
import com.tema.ia.ui.Load
import com.tema.ia.ui.Loader
import com.tema.ia.ui.UiText
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.launch

sealed interface SearchState {
    data object Idle : SearchState
    data object Searching : SearchState
    data class Found(val hits: List<SearchHit>) : SearchState
    data class Failed(val error: UiText) : SearchState
}

enum class MemoryTab { Facts, People, Notes, Skills }

class MemoryViewModel(private val api: SenaApi, private val state: SavedStateHandle) : ViewModel() {
    val facts = Loader(viewModelScope, 15_000L) { api.facts() }
    val people = Loader(viewModelScope, 30_000L) { api.people() }
    val notes = Loader(viewModelScope, 60_000L) { api.notes() }   // the list opens every note for its preview on the Mac: a minute is plenty
    val skills = Loader(viewModelScope, 30_000L) { api.skills() }

    fun loaderOf(tab: MemoryTab): Loader<*> = when (tab) {
        MemoryTab.Facts -> facts
        MemoryTab.People -> people
        MemoryTab.Notes -> notes
        MemoryTab.Skills -> skills
    }

    // ---------- search ----------

    private val _query = MutableStateFlow(state.get<String>("query") ?: "")
    val query: StateFlow<String> = _query.asStateFlow()
    private val _search = MutableStateFlow<SearchState>(SearchState.Idle)
    val search: StateFlow<SearchState> = _search.asStateFlow()

    init {
        viewModelScope.launch { runSearch() }
    }

    @OptIn(FlowPreview::class)
    private suspend fun runSearch() {
        _query.debounce(350).collectLatest { q ->
            if (q.isBlank()) {
                _search.value = SearchState.Idle
                return@collectLatest
            }
            _search.value = SearchState.Searching
            _search.value = try {
                SearchState.Found(api.search(q.trim()))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                SearchState.Failed(e.toUiText())
            }
        }
    }

    fun onQuery(value: String) {
        state["query"] = value
        _query.value = value
    }

    // ---------- facts ----------

    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()
    private val _saving = MutableStateFlow(false)
    val saving: StateFlow<Boolean> = _saving.asStateFlow()

    private fun write(done: UiText, loader: Loader<*>, block: suspend () -> Boolean) {
        viewModelScope.launch {
            _saving.value = true
            try {
                if (block()) _messages.tryEmit(done)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                _saving.value = false
                loader.refresh()
            }
        }
    }

    /** One fact at a time, through Sena's own door: she stamps it and checks it, and nothing she wrote meanwhile is lost. */
    fun addFact(section: String, text: String, onDone: () -> Unit = {}) {
        val item = text.trim()
        if (item.isEmpty()) return
        write(UiText.Res(R.string.memory_fact_added), facts) {
            api.addFact(section.trim(), item)
            onDone()
            true
        }
    }

    fun deleteFact(section: String, item: String) {
        write(UiText.Res(R.string.memory_fact_deleted), facts) {
            api.deleteFact(section, item)
            true
        }
    }

    /** [old] — the line as the server has it (with Sena's stamp, if any); [newText] — the words the owner typed instead. */
    fun editFact(section: String, old: String, newText: String, onDone: () -> Unit = {}) {
        val text = newText.trim()
        if (text.isEmpty() || text == factLine(old).text) { onDone(); return }
        write(UiText.Res(R.string.memory_fact_edited), facts) {
            api.editFact(section, old, text)
            onDone()
            true
        }
    }

    // ---------- people ----------

    /** A new card with every other name the owner typed ("Оля, Олечка": both). */
    fun addPerson(name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, onDone: () -> Unit = {}) {
        if (name.isBlank()) return
        write(UiText.Res(R.string.memory_person_added), people) {
            api.addPerson(name.trim(), aliases.map { it.trim() }.filter { it.isNotEmpty() }.distinct(), relationship.trim(), opinion.trim(), facts)
            onDone()
            true
        }
    }

    /**
     * The card as the owner left it: [aliases] is the whole new list; the facts he erased or changed go as no longer true (the
     * server keeps them with the moment they stopped holding), and only the facts the card doesn't have yet go as new — so a
     * shortened fact is the old one retired and the new one added, never read as "already known, said fuller".
     */
    fun updatePerson(person: Person, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, onDone: () -> Unit = {}) {
        if (name.isBlank()) return
        val (added, retired) = factChanges(person.facts, facts)
        write(UiText.Res(R.string.memory_person_updated), people) {
            api.updatePerson(person.id, name.trim(), aliases.map { it.trim() }.filter { it.isNotEmpty() }.distinct(), relationship.trim(), opinion.trim(), added, retired)
            onDone()
            true
        }
    }

    fun deletePerson(id: String, onDone: () -> Unit = {}) {
        write(UiText.Res(R.string.memory_person_deleted), people) {
            api.deletePerson(id)
            onDone()
            true
        }
    }

    // ---------- notes and skills ----------

    fun createNote(title: String, text: String, onDone: () -> Unit = {}) {
        val name = title.replace(Regex("[\\\\/:*?\"<>|]"), " ").replace(Regex("\\s+"), " ").trim()
        if (name.isEmpty()) return
        write(UiText.Res(R.string.memory_note_created), notes) {
            api.writeNote("$INBOX_FOLDER/$name.md", if (text.isBlank()) "# $name\n" else text)
            onDone()
            true
        }
    }

    fun saveNote(path: String, text: String, onDone: () -> Unit = {}) {
        write(UiText.Res(R.string.memory_note_saved), notes) {
            api.writeNote(path, text, mode = "replace")
            onDone()
            true
        }
    }

    fun deleteNote(path: String, onDone: () -> Unit = {}) {
        write(UiText.Res(R.string.memory_note_deleted), notes) {
            api.deleteNote(path)
            onDone()
            true
        }
    }

    private val _skill = MutableStateFlow<Load<SkillDetail>?>(null)
    val skill: StateFlow<Load<SkillDetail>?> = _skill.asStateFlow()

    fun openSkill(name: String) {
        _skill.value = Load.Loading
        viewModelScope.launch {
            _skill.value = try {
                Load.Ready(api.skill(name))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Load.Failed(e)
            }
        }
    }

    fun closeSkill() { _skill.value = null }

    fun saveSkill(name: String, whenText: String, recipe: String, onDone: () -> Unit = {}) {
        if (name.isBlank() || whenText.isBlank() || recipe.isBlank()) return
        write(UiText.Res(R.string.memory_skill_saved), skills) {
            api.writeSkill(name.trim(), whenText.trim(), recipe.trim())
            onDone()
            true
        }
    }

    fun deleteSkill(name: String, onDone: () -> Unit = {}) {
        write(UiText.Res(R.string.memory_skill_deleted), skills) {
            api.deleteSkill(name)
            onDone()
            true
        }
    }
}

/** One note in full: Sena's own block of it (people, memory, the diary) read-only, the owner's part to edit. */
class NoteViewModel(private val api: SenaApi, val path: String) : ViewModel() {
    val loader = Loader(viewModelScope, 0L) { noteParts(api.noteText(path)) }
    private val _saving = MutableStateFlow(false)
    val saving: StateFlow<Boolean> = _saving.asStateFlow()
    private val _messages = MutableSharedFlow<UiText>(extraBufferCapacity = 8)
    val messages: SharedFlow<UiText> = _messages.asSharedFlow()

    /** [own] — his part only (the whole note when Sena keeps no block in it): the server writes it after her block, which stays hers. */
    fun save(own: String, onDone: () -> Unit = {}) {
        viewModelScope.launch {
            _saving.value = true
            try {
                api.writeNote(path, own, mode = "replace")
                loader.refresh()
                onDone()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                _saving.value = false
            }
        }
    }

    fun delete(onDone: () -> Unit) {
        viewModelScope.launch {
            _saving.value = true
            try {
                api.deleteNote(path)
                onDone()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(e.toUiText())
            } finally {
                _saving.value = false
            }
        }
    }
}

fun NoteInfo.displayTitle(): String = title.ifBlank { path.substringAfterLast('/').removeSuffix(".md") }

/** Where a search hit leads (the registry's kinds, sena/memory/index.py KINDS); null — nowhere to open on the phone, it stays a row. */
enum class SearchTarget { Note, Task, Person, Skill, Facts, Chat }

fun searchTarget(hit: SearchHit): SearchTarget? = when (hit.kind) {
    SearchKind.NOTE, SearchKind.PROJECT, SearchKind.ORGANIZATION, SearchKind.PLACE -> SearchTarget.Note.takeIf { hit.path != null || hit.ref != null }
    SearchKind.TASK -> SearchTarget.Task.takeIf { hit.ref != null }
    SearchKind.PERSON -> SearchTarget.Person.takeIf { hit.ref != null }
    SearchKind.SKILL -> SearchTarget.Skill.takeIf { hit.ref != null }
    SearchKind.FACT -> SearchTarget.Facts
    SearchKind.MESSAGE -> SearchTarget.Chat
    else -> null
}

/**
 * A card's facts as the owner left them against what the card holds: (new — not on the card yet, retired — on the card and no
 * longer in his list). Matched without case and spaces at the ends; a fact he only shortened or reworded is both.
 */
fun factChanges(old: List<String>, edited: List<String>): Pair<List<String>, List<String>> {
    val kept = edited.map { it.trim() }.filter { it.isNotEmpty() }.distinctBy { it.lowercase() }
    val had = old.map { it.trim().lowercase() }.toSet()
    val added = kept.filter { it.lowercase() !in had }
    val retired = old.filter { o -> kept.none { it.equals(o.trim(), ignoreCase = true) } }
    return added to retired
}
