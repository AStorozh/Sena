package com.tema.ia.ui.screens.memory

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.DEFAULT_FACT_SECTION
import com.tema.ia.data.FactSection
import com.tema.ia.data.NoteInfo
import com.tema.ia.data.Person
import com.tema.ia.data.SearchHit
import com.tema.ia.data.SearchKind
import com.tema.ia.data.SkillDetail
import com.tema.ia.data.SkillInfo
import com.tema.ia.data.factLine
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.Dot
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.Initial
import com.tema.ia.ui.components.LeadCard
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SearchField
import com.tema.ia.ui.components.SegmentTabs
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.SkeletonCard
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.problem
import com.tema.ia.ui.simpleMarkdown
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText

@Composable
fun MemoryScreen(viewModel: MemoryViewModel, onOpenNote: (NoteInfo) -> Unit, onOpenTask: (String) -> Unit = {}, onOpenChat: () -> Unit = {}) {
    var tabIndex by rememberSaveable { mutableIntStateOf(0) }
    val tab = MemoryTab.entries[tabIndex]
    val query by viewModel.query.collectAsStateWithLifecycle()
    val search by viewModel.search.collectAsStateWithLifecycle()
    val saving by viewModel.saving.collectAsStateWithLifecycle()
    val skill by viewModel.skill.collectAsStateWithLifecycle()
    val factsState by viewModel.facts.state.collectAsStateWithLifecycle()
    var addFact by rememberSaveable { mutableStateOf(false) }
    var addNote by rememberSaveable { mutableStateOf(false) }
    var deleteFact by rememberSaveable { mutableStateOf<Pair<String, String>?>(null) }
    var editFact by rememberSaveable { mutableStateOf<Pair<String, String>?>(null) }
    var addPerson by rememberSaveable { mutableStateOf(false) }
    var editPersonId by rememberSaveable { mutableStateOf<String?>(null) }   // by id: an open card survives a rotation and shows the card as it is now
    var addSkill by rememberSaveable { mutableStateOf(false) }
    val loader = viewModel.loaderOf(tab)
    val loadState by loader.state.collectAsStateWithLifecycle()
    val peopleState by viewModel.people.state.collectAsStateWithLifecycle()
    val person = { id: String? -> id?.let { (peopleState as? Load.Ready)?.value?.firstOrNull { p -> p.id == it } } }
    PollWhileVisible(loader)
    CollectMessages(viewModel.messages)
    val colors = SenaTheme.colors
    val unnamed = stringResource(R.string.memory_person_unnamed)

    deleteFact?.let { (section, item) ->
        ConfirmDialog(stringResource(R.string.memory_delete_fact_title), stringResource(R.string.memory_delete_fact_text, factLine(item).text), stringResource(R.string.action_delete),
            onConfirm = { viewModel.deleteFact(section, item); deleteFact = null }, onDismiss = { deleteFact = null }, destructive = true)
    }
    if (addFact) {
        val sections = (factsState as? Load.Ready)?.value.orEmpty().map { it.title }.distinct()
        AddFactSheet(sections, saving, { addFact = false }) { section, text -> viewModel.addFact(section, text) { addFact = false } }
    }
    editFact?.let { (section, item) ->
        EditFactSheet(factLine(item).text, saving, { editFact = null }) { text -> viewModel.editFact(section, item, text) { editFact = null } }
    }
    if (addNote) AddNoteSheet(saving, { addNote = false }) { title, text -> viewModel.createNote(title, text) { addNote = false } }
    if (addPerson) PersonSheet(null, saving, { addPerson = false }) { name, aliases, relationship, opinion, facts ->
        viewModel.addPerson(name, aliases, relationship, opinion, facts) { addPerson = false }
    }
    person(editPersonId)?.let { p ->
        // the sheet asks before deleting: one question is enough
        PersonSheet(p, saving, { editPersonId = null }, onDelete = { viewModel.deletePerson(p.id) { editPersonId = null } }) { name, aliases, relationship, opinion, facts ->
            viewModel.updatePerson(p, name, aliases, relationship, opinion, facts) { editPersonId = null }
        }
    }
    if (addSkill) AddSkillSheet(saving, { addSkill = false }) { name, whenText, recipe -> viewModel.saveSkill(name, whenText, recipe) { addSkill = false } }
    skill?.let { SkillSheet(it, saving, viewModel::closeSkill, onSave = { whenText, recipe -> val n = (it as? Load.Ready)?.value?.name; if (n != null) viewModel.saveSkill(n, whenText, recipe, viewModel::closeSkill) }, onDelete = { val n = (it as? Load.Ready)?.value?.name; if (n != null) viewModel.deleteSkill(n, viewModel::closeSkill) }) }

    SenaScreen(
        title = stringResource(R.string.memory_title),
        nav = true,
        problem = loadState.problem,
        onRetry = loader::refresh,
        leading = { SenaLeaf(size = Size.leafTop, tint = colors.onHeader, description = stringResource(R.string.cd_leaf)) },
        actions = {
            if (query.isBlank()) {
                val label = when (tab) {
                    MemoryTab.Facts -> R.string.memory_add_fact
                    MemoryTab.People -> R.string.memory_person_new
                    MemoryTab.Notes -> R.string.memory_note_new
                    MemoryTab.Skills -> R.string.memory_skill_new
                }
                SenaIconButton(
                    R.drawable.ic_plus,
                    stringResource(label),
                    {
                        when (tab) {
                            MemoryTab.Facts -> addFact = true
                            MemoryTab.People -> addPerson = true
                            MemoryTab.Notes -> addNote = true
                            MemoryTab.Skills -> addSkill = true
                        }
                    },
                    Modifier.testTag("memory_add"),
                    tint = colors.onHeader,
                    size = Size.touchHeader,
                )
            }
        },
    ) {
        SearchField(
            query, viewModel::onQuery, stringResource(R.string.memory_search_hint),
            Modifier.fillMaxWidth().padding(horizontal = Space.screen).padding(top = Space.screen).testTag("memory_search"),
        )
        if (query.isNotBlank()) {
            // a hit leads to where it lives: a note opens, a task's page, a person's card, a skill, the facts, the conversation
            SearchResults(search) { hit ->
                when (searchTarget(hit)) {
                    SearchTarget.Note -> onOpenNote(NoteInfo(hit.path ?: hit.ref.orEmpty().substringBefore('#'), hit.title.orEmpty(), "", null, ""))
                    SearchTarget.Task -> hit.ref?.let(onOpenTask)
                    SearchTarget.Person -> { viewModel.onQuery(""); tabIndex = MemoryTab.People.ordinal; editPersonId = hit.ref?.substringBefore(':') }
                    SearchTarget.Skill -> { viewModel.onQuery(""); tabIndex = MemoryTab.Skills.ordinal; hit.ref?.let(viewModel::openSkill) }
                    SearchTarget.Facts -> { viewModel.onQuery(""); tabIndex = MemoryTab.Facts.ordinal }
                    SearchTarget.Chat -> onOpenChat()
                    null -> Unit
                }
            }
        } else {
            SegmentTabs(
                listOf(R.string.memory_tab_facts, R.string.memory_tab_people, R.string.memory_tab_notes, R.string.memory_tab_skills).map { stringResource(it) },
                tabIndex, { tabIndex = it }, Modifier.padding(top = Space.gap),
            )
            PullRefresh(loader, modifier = Modifier.weight(1f)) {
                when (tab) {
                    MemoryTab.Facts -> Content(factsState, viewModel.facts::refresh) { sections ->
                        FactsTab(sections, onEdit = { s, i -> editFact = s to i }, onDelete = { s, i -> deleteFact = s to i })
                    }
                    MemoryTab.People -> Content(peopleState, viewModel.people::refresh) { PeopleTab(it, unnamed) { p -> editPersonId = p.id } }
                    MemoryTab.Notes -> Content(viewModel.notes.state.collectAsStateWithLifecycle().value, viewModel.notes::refresh) { NotesTab(it, onOpenNote) }
                    MemoryTab.Skills -> Content(viewModel.skills.state.collectAsStateWithLifecycle().value, viewModel.skills::refresh) { SkillsTab(it, viewModel::openSkill) }
                }
            }
        }
    }
}

@Composable
private fun <T> Content(load: Load<T>, retry: () -> Unit, content: @Composable (T) -> Unit) {
    when (load) {
        is Load.Loading -> LoadingBox()
        is Load.Failed -> ErrorState(load.error.toUiText().asString(), retry)
        is Load.Ready -> content(load.value)
    }
}

@Composable
private fun SearchResults(state: SearchState, onOpen: (SearchHit) -> Unit) {
    when (state) {
        SearchState.Idle, SearchState.Searching -> LoadingBox()
        is SearchState.Failed -> ErrorState(state.error.asString(), {})
        is SearchState.Found -> if (state.hits.isEmpty()) {
            EmptyState(R.drawable.ic_search, stringResource(R.string.memory_search_empty))
        } else {
            val res = LocalContext.current.resources
            val colors = SenaTheme.colors
            LazyColumn(Modifier.fillMaxSize().testTag("memory_results"), contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
                itemsIndexed(state.hits, key = { i, h -> i.toString() + h.text.hashCode() }) { index, hit ->
                    val kind = searchKindLabel(hit.kind)
                    RowCard(
                        title = hit.title?.let { "$it: ${hit.text}" } ?: hit.text,
                        modifier = Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "hit-" + index + hit.text.hashCode()).testTag("memory_hit"), maxTitleLines = 4,
                        subtitle = listOfNotNull(kind, hit.time?.let { Fmt.ago(res, it, System.currentTimeMillis() / 1000.0) }).joinToString(" · "),
                        onClick = if (searchTarget(hit) != null) ({ onOpen(hit) }) else null,
                        leading = { IconTile(R.drawable.ic_memory, colors.tintOf(hit.kind)) },
                    )
                }
            }
        }
    }
}

@Composable
private fun FactsTab(sections: List<FactSection>, onEdit: (String, String) -> Unit, onDelete: (String, String) -> Unit) {
    if (sections.isEmpty()) {
        EmptyState(R.drawable.ic_memory, stringResource(R.string.memory_facts_empty), hint = stringResource(R.string.memory_facts_empty_hint))
        return
    }
    val colors = SenaTheme.colors
    LazyColumn(Modifier.fillMaxSize().testTag("facts_list"), contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        // memory.md is written by hand too, so a section title may repeat: the place in the list keeps the keys apart
        itemsIndexed(sections, key = { i, s -> "$i:${s.title}" }) { index, section ->
            SenaCard(Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "fact-$index-" + section.title).animateContentSize(), padding = Space.inset) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
                    Dot(colors.tint(index), size = Size.dotBig)
                    Text(section.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    Muted(section.items.size.toString(), style = MaterialTheme.typography.labelMedium)
                }
                Column {
                    section.items.forEach { item ->
                        val line = factLine(item)
                        Row(
                            Modifier.fillMaxWidth().clickable { onEdit(section.title, item) },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f).padding(vertical = Space.small)) {
                                Text(line.text, style = MaterialTheme.typography.bodyLarge)
                                line.stamp?.let { Muted(it, style = MaterialTheme.typography.labelSmall) }
                            }
                            SenaIconButton(R.drawable.ic_trash, stringResource(R.string.cd_delete), { onDelete(section.title, item) }, tint = colors.textMuted)
                        }
                    }
                }
            }
        }
    }
}

/** A card's title: the name, else the first other name, else "no name yet" (someone known only by voice). */
private fun Person.shownName(unnamed: String): String = name.ifBlank { aliases.firstOrNull().orEmpty() }.ifBlank { unnamed }

@Composable
private fun PeopleTab(people: List<Person>, unnamed: String, onOpen: (Person) -> Unit) {
    if (people.isEmpty()) {
        EmptyState(R.drawable.ic_person, stringResource(R.string.memory_people_empty), hint = stringResource(R.string.memory_people_empty_hint))
        return
    }
    val colors = SenaTheme.colors
    LazyColumn(Modifier.fillMaxSize().testTag("people_list"), contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        itemsIndexed(people, key = { _, p -> p.id + p.name }) { index, p ->
            SenaCard(Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "person-" + p.id + p.name).testTag("person_row"), onClick = { onOpen(p) }, padding = Space.inset) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
                    Initial(p.shownName(unnamed), colors.tint(index + 1))
                    Column(Modifier.weight(1f)) {
                        Text(p.shownName(unnamed), style = MaterialTheme.typography.titleMedium)
                        if (p.relationship.isNotBlank()) Muted(p.relationship)
                    }
                }
                if (p.aliases.isNotEmpty()) Muted(stringResource(R.string.memory_person_aliases, p.aliases.joinToString(", ")))
                if (p.opinion.isNotBlank()) Text(p.opinion, style = MaterialTheme.typography.bodyLarge)
                p.facts.forEach { Text(stringResource(R.string.bullet, it), style = MaterialTheme.typography.bodyMedium) }
            }
        }
    }
}

@Composable
private fun NotesTab(notes: List<NoteInfo>, onOpen: (NoteInfo) -> Unit) {
    if (notes.isEmpty()) {
        EmptyState(R.drawable.ic_note, stringResource(R.string.memory_notes_empty), hint = stringResource(R.string.memory_notes_empty_hint))
        return
    }
    val res = LocalContext.current.resources
    val colors = SenaTheme.colors
    LazyColumn(Modifier.fillMaxSize().testTag("notes_list"), contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        itemsIndexed(notes, key = { _, n -> n.path }) { index, note ->
            RowCard(
                title = note.displayTitle(), modifier = Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "note-" + note.path).testTag("note_row"),
                subtitle = listOfNotNull(note.folder.ifBlank { null }, note.updated?.let { Fmt.ago(res, it, System.currentTimeMillis() / 1000.0) }).joinToString(" · "),
                onClick = { onOpen(note) },
                leading = { IconTile(R.drawable.ic_note, colors.tintOf(note.folder)) },
                below = { if (note.preview.isNotBlank()) Text(note.preview, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) },
            )
        }
    }
}

@Composable
private fun SkillsTab(skills: List<SkillInfo>, onOpen: (String) -> Unit) {
    if (skills.isEmpty()) {
        EmptyState(R.drawable.ic_bolt, stringResource(R.string.memory_skills_empty), hint = stringResource(R.string.memory_skills_empty_hint))
        return
    }
    val colors = SenaTheme.colors
    LazyColumn(Modifier.fillMaxSize().testTag("skills_list"), contentPadding = listPadding(top = Space.gap), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        itemsIndexed(skills, key = { _, s -> s.name }) { index, skill ->
            RowCard(
                title = skill.name, subtitle = skill.whenText, modifier = Modifier.fillMaxWidth().animateItem(fadeInSpec = null).rise(index, "skill-" + skill.name).testTag("skill_row"),
                onClick = { onOpen(skill.name) }, leading = { IconTile(R.drawable.ic_bolt, colors.tint(index)) },
            )
        }
    }
}

@Composable
private fun SkillSheet(load: Load<SkillDetail>, saving: Boolean, onDismiss: () -> Unit, onSave: (String, String) -> Unit, onDelete: () -> Unit) {
    var deleting by remember { mutableStateOf(false) }
    if (deleting) {
        ConfirmDialog(
            stringResource(R.string.memory_delete_skill_title), stringResource(R.string.memory_delete_skill_text, (load as? Load.Ready)?.value?.name.orEmpty()),
            stringResource(R.string.action_delete), onConfirm = { deleting = false; onDelete() }, onDismiss = { deleting = false }, destructive = true,
        )
    }
    SenaSheet(((load as? Load.Ready)?.value?.name) ?: stringResource(R.string.memory_tab_skills), onDismiss) {
        when (load) {
            is Load.Loading -> Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) { SkeletonCard(); SkeletonCard() }
            is Load.Failed -> Text(load.error.toUiText().asString(), style = MaterialTheme.typography.bodyLarge)
            is Load.Ready -> {
                var whenText by rememberSaveable(load.value.name) { mutableStateOf(load.value.whenText) }
                var recipe by rememberSaveable(load.value.name) { mutableStateOf(load.value.recipe) }
                Column(verticalArrangement = Arrangement.spacedBy(Space.gap)) {
                    SenaField(whenText, { whenText = it }, Modifier.fillMaxWidth().testTag("skill_when"), label = stringResource(R.string.memory_skill_when), minLines = 1, maxLines = 3)
                    SenaField(recipe, { recipe = it }, Modifier.fillMaxWidth().testTag("skill_recipe"), label = stringResource(R.string.memory_skill_recipe), minLines = 4, maxLines = 10)
                    SenaButton(
                        stringResource(R.string.action_save), { onSave(whenText, recipe) }, Modifier.fillMaxWidth().testTag("skill_save"),
                        enabled = whenText.isNotBlank() && recipe.isNotBlank(), busy = saving, large = true,
                    )
                    SenaButton(stringResource(R.string.action_delete), { deleting = true }, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
                }
            }
        }
    }
}

@Composable
private fun AddSkillSheet(saving: Boolean, onDismiss: () -> Unit, onSave: (String, String, String) -> Unit) {
    var name by rememberSaveable { mutableStateOf("") }
    var whenText by rememberSaveable { mutableStateOf("") }
    var recipe by rememberSaveable { mutableStateOf("") }
    SenaSheet(stringResource(R.string.memory_skill_new), onDismiss) {
        SenaField(name, { name = it }, Modifier.fillMaxWidth().testTag("skill_name"), label = stringResource(R.string.memory_skill_name), placeholder = stringResource(R.string.memory_skill_name_hint), singleLine = true)
        SenaField(whenText, { whenText = it }, Modifier.fillMaxWidth().testTag("skill_when"), label = stringResource(R.string.memory_skill_when), placeholder = stringResource(R.string.memory_skill_when_hint), minLines = 1, maxLines = 3)
        SenaField(recipe, { recipe = it }, Modifier.fillMaxWidth().testTag("skill_recipe"), label = stringResource(R.string.memory_skill_recipe), placeholder = stringResource(R.string.memory_skill_recipe_hint), minLines = 4, maxLines = 10)
        SenaButton(
            stringResource(R.string.action_save), { onSave(name, whenText, recipe) }, Modifier.fillMaxWidth().testTag("skill_save"),
            enabled = name.isNotBlank() && whenText.isNotBlank() && recipe.isNotBlank(), busy = saving, large = true,
        )
    }
}

@Composable
private fun EditFactSheet(initial: String, saving: Boolean, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf(initial) }
    SenaSheet(stringResource(R.string.memory_edit_fact), onDismiss) {
        SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("fact_text"), minLines = 2, maxLines = 5)
        SenaButton(stringResource(R.string.action_save), { onSave(text) }, Modifier.fillMaxWidth().testTag("fact_save"), enabled = text.isNotBlank(), busy = saving, large = true)
    }
}

/** A person's card: every other name in one field, comma-separated (what's erased there is gone), facts one per line. */
@Composable
private fun PersonSheet(person: Person?, saving: Boolean, onDismiss: () -> Unit, onDelete: (() -> Unit)? = null, onSave: (String, List<String>, String, String, List<String>) -> Unit) {
    var name by rememberSaveable { mutableStateOf(person?.name.orEmpty()) }
    var aliases by rememberSaveable { mutableStateOf(person?.aliases.orEmpty().joinToString(", ")) }
    var relationship by rememberSaveable { mutableStateOf(person?.relationship.orEmpty()) }
    var opinion by rememberSaveable { mutableStateOf(person?.opinion.orEmpty()) }
    var factsText by rememberSaveable { mutableStateOf(person?.facts?.joinToString("\n").orEmpty()) }
    var deleting by rememberSaveable { mutableStateOf(false) }
    if (deleting && onDelete != null) {
        ConfirmDialog(
            stringResource(R.string.memory_delete_person_title), stringResource(R.string.memory_delete_person_text, person?.name.orEmpty()),
            stringResource(R.string.action_delete), onConfirm = { deleting = false; onDelete() }, onDismiss = { deleting = false }, destructive = true,
        )
    }
    SenaSheet(stringResource(if (person == null) R.string.memory_person_new else R.string.memory_person_edit), onDismiss) {
        SenaField(name, { name = it }, Modifier.fillMaxWidth().testTag("person_name"), label = stringResource(R.string.memory_person_name), singleLine = true)
        SenaField(aliases, { aliases = it }, Modifier.fillMaxWidth().testTag("person_alias"), label = stringResource(R.string.memory_person_alias_hint), singleLine = true)
        SenaField(relationship, { relationship = it }, Modifier.fillMaxWidth().testTag("person_relationship"), label = stringResource(R.string.memory_person_relationship), singleLine = true)
        SenaField(opinion, { opinion = it }, Modifier.fillMaxWidth().testTag("person_opinion"), label = stringResource(R.string.memory_person_opinion), minLines = 1, maxLines = 3)
        SenaField(factsText, { factsText = it }, Modifier.fillMaxWidth().testTag("person_facts"), label = stringResource(R.string.memory_person_facts), placeholder = stringResource(R.string.memory_person_facts_hint), minLines = 2, maxLines = 6)
        SenaButton(
            stringResource(R.string.action_save),
            { onSave(name, aliases.split(',').map { it.trim() }.filter { it.isNotEmpty() }, relationship, opinion, factsText.lines().map { it.trim() }.filter { it.isNotEmpty() }) },
            Modifier.fillMaxWidth().testTag("person_save"), enabled = name.isNotBlank(), busy = saving, large = true,
        )
        if (onDelete != null) SenaButton(stringResource(R.string.action_delete), { deleting = true }, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
    }
}

@Composable
private fun AddFactSheet(sections: List<String>, saving: Boolean, onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    // What's shown is the translated field label; DEFAULT_FACT_SECTION is what's sent to the server, since section names aren't translated (like FOLDER_RU in Mac).
    val defaultSectionLabel = stringResource(R.string.memory_default_section)
    var section by rememberSaveable { mutableStateOf(sections.firstOrNull() ?: defaultSectionLabel) }
    var text by rememberSaveable { mutableStateOf("") }
    SenaSheet(stringResource(R.string.memory_add_fact), onDismiss) {
        SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("fact_text"), placeholder = stringResource(R.string.memory_fact_hint), minLines = 2, maxLines = 5)
        SenaField(section, { section = it }, Modifier.fillMaxWidth().testTag("fact_section"), label = stringResource(R.string.memory_fact_section), singleLine = true)
        if (sections.isNotEmpty()) {
            val shown = sections.take(3)
            SegmentTabs(shown, shown.indexOf(section), { section = shown[it] }, inset = false)
        }
        SenaButton(
            stringResource(R.string.action_save),
            {
                val chosen = section.trim().ifBlank { defaultSectionLabel }
                onSave(if (chosen == defaultSectionLabel) DEFAULT_FACT_SECTION else chosen, text)
            },
            Modifier.fillMaxWidth().testTag("fact_save"), enabled = text.isNotBlank(), busy = saving, large = true,
        )
    }
}

@Composable
private fun AddNoteSheet(saving: Boolean, onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var title by rememberSaveable { mutableStateOf("") }
    var text by rememberSaveable { mutableStateOf("") }
    SenaSheet(stringResource(R.string.memory_note_new), onDismiss) {
        SenaField(title, { title = it }, Modifier.fillMaxWidth().testTag("note_title"), placeholder = stringResource(R.string.memory_note_title_hint), singleLine = true)
        SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("note_text"), placeholder = stringResource(R.string.memory_note_text_hint), minLines = 4, maxLines = 8)
        SenaButton(stringResource(R.string.action_save), { onSave(title, text) }, Modifier.fillMaxWidth().testTag("note_save"), enabled = title.isNotBlank(), busy = saving, large = true)
    }
}

@Composable
fun NoteScreen(viewModel: NoteViewModel, title: String, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val saving by viewModel.saving.collectAsStateWithLifecycle()
    var editing by rememberSaveable { mutableStateOf(false) }
    var deleting by rememberSaveable { mutableStateOf(false) }
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)
    if (deleting) {
        ConfirmDialog(
            stringResource(R.string.memory_delete_note_title), stringResource(R.string.memory_delete_note_text, title), stringResource(R.string.action_delete),
            onConfirm = { deleting = false; viewModel.delete(onBack) }, onDismiss = { deleting = false }, destructive = true,
        )
    }
    SenaScreen(
        title = title,
        onBack = onBack,
        problem = load.problem,
        onRetry = viewModel.loader::refresh,
    ) {
        when (val l = load) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current),
                verticalArrangement = Arrangement.spacedBy(Space.gap),
            ) {
                val parts = l.value
                // Sena's own block (a person's card, memory, the diary): read here, never edited — only his part below it is
                parts.auto?.let { AutoBlock(it) }
                if (editing) {
                    var text by rememberSaveable(parts.own) { mutableStateOf(parts.own) }
                    SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("note_edit_text"), minLines = 6, maxLines = 20)
                    SenaButton(
                        stringResource(R.string.action_save), { viewModel.save(text) { editing = false } }, Modifier.fillMaxWidth().testTag("note_save_edit"),
                        enabled = parts.auto != null || text.isNotBlank(), busy = saving, large = true,   // under her block his part may be emptied
                    )
                    SenaButton(stringResource(R.string.action_cancel), { editing = false }, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
                } else {
                    SenaCard(Modifier.fillMaxWidth().rise(0, "note-body").testTag("note_body")) {
                        if (parts.own.isBlank() && parts.auto != null) Muted(stringResource(R.string.note_own_empty), style = MaterialTheme.typography.bodyLarge)
                        else Text(simpleMarkdown(parts.own), style = MaterialTheme.typography.bodyLarge)
                    }
                    SenaButton(stringResource(R.string.action_edit), { editing = true }, Modifier.fillMaxWidth().testTag("note_edit"), style = ButtonStyle.Outline)
                    SenaButton(stringResource(R.string.action_delete), { deleting = true }, Modifier.fillMaxWidth().testTag("note_delete"), style = ButtonStyle.Outline)
                }
            }
        }
    }
}

/** Sena's block of a note she keeps, folded by default: what she wrote there, without its markers and its frontmatter. */
@Composable
private fun AutoBlock(auto: String) {
    var open by rememberSaveable { mutableStateOf(false) }
    val shown = remember(auto) { autoText(auto) }
    SenaCard(Modifier.fillMaxWidth().animateContentSize().testTag("note_auto")) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
            Muted(stringResource(R.string.note_auto_title), Modifier.weight(1f), style = MaterialTheme.typography.labelLarge)
            SenaButton(stringResource(if (open) R.string.note_auto_hide else R.string.note_auto_show), { open = !open }, Modifier.testTag("note_auto_toggle"), style = ButtonStyle.Text, compact = true)
        }
        if (open) Text(simpleMarkdown(shown), style = MaterialTheme.typography.bodyMedium, color = SenaTheme.colors.textMuted)
    }
}

private val frontmatter = Regex("""\A---\n.*?\n---\n?""", RegexOption.DOT_MATCHES_ALL)

/** Sena's block for reading: its markers and the note's frontmatter (sena: auto, aliases) are for the server, not for him. */
internal fun autoText(auto: String): String =
    frontmatter.replace(auto.trimStart(), "").replace(com.tema.ia.data.NOTE_BEGIN, "").replace(com.tema.ia.data.NOTE_END, "").trim()

/** A search kind in the app's language: the server names kinds in Russian (index.KINDS); an unknown one is shown as it came. */
@Composable
private fun searchKindLabel(kind: String): String = when (kind) {
    SearchKind.TASK -> stringResource(R.string.search_kind_task)
    SearchKind.FACT -> stringResource(R.string.search_kind_fact)
    SearchKind.PERSON -> stringResource(R.string.search_kind_person)
    SearchKind.NOTE -> stringResource(R.string.search_kind_note)
    SearchKind.RULE -> stringResource(R.string.search_kind_rule)
    SearchKind.SKILL -> stringResource(R.string.search_kind_skill)
    SearchKind.LESSON -> stringResource(R.string.search_kind_lesson)
    SearchKind.GOAL -> stringResource(R.string.search_kind_goal)
    SearchKind.MESSAGE -> stringResource(R.string.search_kind_message)
    SearchKind.HEARD -> stringResource(R.string.search_kind_heard)
    SearchKind.FILE -> stringResource(R.string.search_kind_file)
    SearchKind.PROJECT -> stringResource(R.string.search_kind_project)
    SearchKind.ORGANIZATION -> stringResource(R.string.search_kind_organization)
    SearchKind.PLACE -> stringResource(R.string.search_kind_place)
    else -> kind
}
