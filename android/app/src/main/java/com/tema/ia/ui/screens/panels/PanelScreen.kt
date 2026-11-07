package com.tema.ia.ui.screens.panels

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.PanelAction
import com.tema.ia.data.PanelBlock
import com.tema.ia.data.PanelField
import com.tema.ia.data.PanelItem
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonRow
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.Dot
import com.tema.ia.ui.components.DotBar
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.HeaderNote
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.RowCard
import com.tema.ia.ui.components.SectionTitle
import com.tema.ia.ui.components.SegmentedControl
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.Mustard
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** A panel the server describes: stats, notes, lists with actions, forms, switches. The app draws, the server decides. */
@Composable
fun PanelScreen(viewModel: PanelViewModel, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val busy by viewModel.busy.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)
    var asking by remember { mutableStateOf<PanelAction?>(null) }      // a question before an action
    var typing by remember { mutableStateOf<PanelAction?>(null) }      // one value the action needs
    val run: (PanelAction) -> Unit = { action ->
        when {
            action.input != null -> typing = action
            action.confirm != null -> asking = action
            else -> viewModel.act(action)
        }
    }
    asking?.let { action ->
        ConfirmDialog(action.label, action.confirm.orEmpty(), action.label, onConfirm = { asking = null; viewModel.act(action) }, onDismiss = { asking = null },
            destructive = action.danger)
    }
    typing?.let { action ->
        InputSheet(action, onDismiss = { typing = null }) { value ->   // typing the value is the deliberate step itself: no second question
            typing = null
            viewModel.act(action, JsonObject(mapOf("value" to JsonPrimitive(value))))
        }
    }

    val panel = (load as? Load.Ready)?.value
    SenaScreen(
        title = panel?.title ?: stringResource(R.string.panel_loading_title),
        onBack = onBack,
        header = panel?.subtitle?.takeIf { it.isNotBlank() }?.let { { HeaderNote(it) } },
        problem = load.problem, onRetry = viewModel.loader::refresh,
    ) {
        when (val l = load) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> PullRefresh(viewModel.loader) {
                LazyColumn(
                    Modifier.fillMaxSize().testTag("panel_" + l.value.id),
                    contentPadding = listPadding(top = Space.screen),
                    verticalArrangement = Arrangement.spacedBy(Space.gap),
                ) {
                    itemsIndexed(l.value.blocks) { index, block ->
                        Block(block, busy, run, viewModel::act, Modifier.rise(index, "panel-${l.value.id}-$index"))
                    }
                }
            }
        }
    }
}

@Composable
private fun Block(block: PanelBlock, busy: String?, run: (PanelAction) -> Unit, submit: (PanelAction, JsonObject, () -> Unit) -> Unit, modifier: Modifier) {
    when (block) {
        is PanelBlock.Stats -> SenaCard(modifier.fillMaxWidth()) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
                block.items.forEach { stat ->
                    Column(Modifier.weight(1f)) {
                        Text(stat.value, style = MaterialTheme.typography.titleLarge, color = toneText(stat.tone), maxLines = 1)
                        Muted(stat.label, style = MaterialTheme.typography.labelMedium, maxLines = 2)
                    }
                }
            }
        }
        is PanelBlock.Note -> Text(block.text, modifier.fillMaxWidth(), style = MaterialTheme.typography.bodyMedium, color = if (block.tone == null) SenaTheme.colors.text else toneText(block.tone))
        is PanelBlock.Section -> SectionTitle(block.title, modifier)
        is PanelBlock.Items -> Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Space.gap)) {
            if (block.items.isEmpty() && block.empty.isNotBlank()) EmptyState(R.drawable.ic_sena_leaf, block.empty)
            block.items.forEach { item -> ItemRow(item, busy, run) }
        }
        is PanelBlock.Form -> FormCard(block, busy, submit, modifier)
        is PanelBlock.Toggle -> RowCard(
            title = block.label, modifier = modifier.fillMaxWidth().testTag("panel_switch"), maxTitleLines = 3,
            trailing = { SenaSwitch(block.value, { submit(block.action, JsonObject(mapOf("value" to JsonPrimitive(it)))) {} }) },
        )
        is PanelBlock.Buttons -> ButtonRow(modifier) {
            block.items.forEach { action -> ActionButton(action, busy, run, primary = true) }
        }
    }
}

@Composable
private fun ItemRow(item: PanelItem, busy: String?, run: (PanelAction) -> Unit) {
    RowCard(
        title = item.title,
        subtitle = listOfNotNull(item.subtitle, item.meta).joinToString(" · ").ifBlank { null },
        modifier = Modifier.fillMaxWidth().testTag("panel_item"),
        maxTitleLines = 4, maxSubtitleLines = 4,
        leading = item.tone?.let { tone -> toneDot(tone)?.let { color -> { Dot(color) } } },
        below = if (item.progress == null && item.actions.isEmpty()) null else {
            {
                item.progress?.let { DotBar(it / 100f) }
                if (item.actions.isNotEmpty()) ButtonRow { item.actions.forEach { ActionButton(it, busy, run, primary = false) } }
            }
        },
    )
}

@Composable
private fun ActionButton(action: PanelAction, busy: String?, run: (PanelAction) -> Unit, primary: Boolean) {
    SenaButton(
        action.label, { run(action) }, Modifier.testTag("panel_action_" + action.id),
        style = when { action.danger -> ButtonStyle.Danger; primary -> ButtonStyle.Primary; else -> ButtonStyle.Outline },
        busy = busy == action.id + action.args, enabled = busy == null, compact = !primary,
    )
}

/** A form the server describes: what's typed survives a rotation (Н-8) and a refusal; it starts clean once the server took it. */
@Composable
private fun FormCard(form: PanelBlock.Form, busy: String?, submit: (PanelAction, JsonObject, () -> Unit) -> Unit, modifier: Modifier) {
    val initial = form.fields.associate { it.name to it.value }
    var values by rememberSaveable(form.id) { mutableStateOf(initial) }
    SenaCard(modifier.fillMaxWidth().testTag("panel_form_" + form.id)) {
        if (form.title.isNotBlank()) SectionTitle(form.title)
        form.fields.forEach { field -> FieldInput(field, values[field.name].orEmpty()) { values = values + (field.name to it) } }
        val filled = form.fields.firstOrNull()?.let { values[it.name].orEmpty().isNotBlank() } ?: true
        SenaButton(form.submit.label, {
            submit(form.submit, JsonObject(values.mapValues { (_, v) -> JsonPrimitive(v) })) { values = initial }
        }, Modifier.fillMaxWidth().testTag("panel_submit_" + form.id), enabled = filled && busy == null, busy = busy == form.submit.id + form.submit.args, large = true)
    }
}

@Composable
private fun FieldInput(field: PanelField, value: String, onChange: (String) -> Unit) {
    when (field.kind) {
        "choice" -> Column(verticalArrangement = Arrangement.spacedBy(Space.small)) {
            Muted(field.label, style = MaterialTheme.typography.labelMedium)
            val selected = field.options.indexOfFirst { it.first == value }.coerceAtLeast(0)
            SegmentedControl(field.options.map { it.second }, selected, { onChange(field.options[it].first) })
        }
        "number" -> SenaField(value, onChange, Modifier.fillMaxWidth(), label = field.label, placeholder = field.placeholder, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
        "multiline" -> SenaField(value, onChange, Modifier.fillMaxWidth(), label = field.label, placeholder = field.placeholder, minLines = 3, maxLines = 8)
        else -> SenaField(value, onChange, Modifier.fillMaxWidth(), label = field.label, placeholder = field.placeholder, singleLine = true)
    }
}

@Composable
private fun InputSheet(action: PanelAction, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    val input = action.input ?: return
    var text by rememberSaveable(action.id) { mutableStateOf(input.value) }
    SenaSheet(action.label, onDismiss) {
        SenaField(text, { text = it }, Modifier.fillMaxWidth().testTag("panel_input"), label = input.label, minLines = if (input.kind == "multiline") 3 else 1,
            maxLines = if (input.kind == "multiline") 8 else 1, singleLine = input.kind != "multiline",
            keyboardOptions = if (input.kind == "number") KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default)
        // an optional input takes an empty answer too: "resume" with nothing to add, an emptied list of tools that need no "yes"
        SenaButton(stringResource(R.string.action_save), { onSave(text) }, Modifier.fillMaxWidth().testTag("panel_input_save"), enabled = input.accepts(text), large = true)
    }
}

@Composable
private fun toneText(tone: String?): Color = when (tone) {
    "good" -> SenaTheme.colors.okText
    "warn" -> SenaTheme.colors.warnText
    "bad" -> SenaTheme.colors.badText
    "muted" -> SenaTheme.colors.textMuted
    else -> SenaTheme.colors.text
}

private fun toneDot(tone: String): Color? = when (tone) {
    "good" -> Lime
    "warn" -> Mustard
    "bad" -> Coral
    else -> null
}
