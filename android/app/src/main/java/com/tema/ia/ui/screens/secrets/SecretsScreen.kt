package com.tema.ia.ui.screens.secrets

import android.content.ClipData
import android.os.PersistableBundle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.LocalClipboard
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.data.SecretInfo
import com.tema.ia.ui.Biometric
import com.tema.ia.ui.Biometric.asFragmentActivity
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Load
import com.tema.ia.ui.LocalSnackbar
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.PullRefresh
import com.tema.ia.ui.SecureWindow
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.Chip
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.EmptyState
import com.tema.ia.ui.components.ErrorState
import com.tema.ia.ui.components.IconTile
import com.tema.ia.ui.components.LoadingBox
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.listPadding
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.launch

/**
 * The owner's passwords and keys for Sena: entered only here, behind the app's own lock; the list never shows values.
 * The screen stays out of screenshots and the recents preview: a value can be shown on it.
 */
@Composable
fun SecretsScreen(viewModel: SecretsViewModel, onBack: () -> Unit) {
    SecureWindow()
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val saving by viewModel.saving.collectAsStateWithLifecycle()
    val revealed by viewModel.revealed.collectAsStateWithLifecycle()
    var adding by rememberSaveable { mutableStateOf(false) }
    var editingName by rememberSaveable { mutableStateOf<String?>(null) }   // by name: the open sheet survives a rotation
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)
    val colors = SenaTheme.colors
    val agents = (load as? Load.Ready)?.value?.agents ?: listOf("ag_mac", "ag_web")
    val editing = editingName?.let { name -> (load as? Load.Ready)?.value?.items?.firstOrNull { it.name == name } }

    if (adding) {
        SecretSheet(null, agents, saving, null, onDismiss = { adding = false }) { name, value, note, allow, reveal, sites ->
            viewModel.save(name, value, note, allow, reveal, sites) { adding = false }
        }
    }
    editing?.let { s ->
        val close = { editingName = null; viewModel.hide() }
        SecretSheet(
            s, agents, saving, revealed?.takeIf { it.first == s.name }?.second, onDismiss = close,
            onReveal = { viewModel.reveal(s.name) }, onRemove = { viewModel.remove(s.name, close) },
        ) { name, value, note, allow, reveal, sites -> viewModel.save(name, value, note, allow, reveal, sites, close) }
    }

    SenaScreen(
        title = stringResource(R.string.more_secrets),
        onBack = onBack,
        problem = load.problem, onRetry = viewModel.loader::refresh,
        actions = {
            SenaIconButton(R.drawable.ic_plus, stringResource(R.string.secrets_add), { adding = true }, Modifier.testTag("secrets_add"), tint = colors.onHeader, size = Size.touchHeader)
        },
    ) {
        when (val l = load) {
            is Load.Loading -> LoadingBox()
            is Load.Failed -> ErrorState(l.error.toUiText().asString(), viewModel.loader::refresh)
            is Load.Ready -> PullRefresh(viewModel.loader) {
                if (l.value.items.isEmpty()) {
                    EmptyState(R.drawable.ic_lock, stringResource(R.string.secrets_empty), hint = stringResource(R.string.secrets_empty_hint))
                } else {
                    LazyColumn(
                        Modifier.fillMaxSize().testTag("secrets_list"),
                        contentPadding = listPadding(top = Space.screen),
                        verticalArrangement = Arrangement.spacedBy(Space.gap),
                    ) {
                        itemsIndexed(l.value.items, key = { _, s -> s.name }) { index, s ->
                            SecretRow(s, Modifier.animateItem(fadeInSpec = null).rise(index, "secret-" + s.name)) { editingName = s.name }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun agentLabel(id: String): String = when (id) {
    "ag_mac" -> stringResource(R.string.secrets_agent_mac)
    "ag_web" -> stringResource(R.string.secrets_agent_web)
    else -> id
}

@Composable
private fun SecretRow(s: SecretInfo, modifier: Modifier, onOpen: () -> Unit) {
    val colors = SenaTheme.colors
    SenaCard(modifier.fillMaxWidth().testTag("secret_row"), onClick = onOpen, padding = Space.inset) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
            IconTile(R.drawable.ic_lock, colors.tintOf(s.name))
            Column(Modifier.weight(1f)) {
                Text(s.name, style = MaterialTheme.typography.titleMedium)
                if (s.note.isNotBlank()) Muted(s.note)
            }
        }
        if (s.allow.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(Space.small)) { s.allow.forEach { Chip(agentLabel(it)) } }
        } else {
            Muted(stringResource(R.string.secrets_nobody))
        }
        if (s.sites.isNotEmpty()) Muted(stringResource(R.string.secrets_sites_only, s.sites.joinToString(", ")))
        if (s.reveal == "never") Muted(stringResource(R.string.secrets_never))
        if (s.changed > 0) Muted(stringResource(R.string.secrets_changed, s.changed))
    }
}

/** One sheet for a new secret and for an existing one: the value field is empty on edit (empty — keep the current one). */
@Composable
private fun SecretSheet(
    existing: SecretInfo?,
    agents: List<String>,
    saving: Boolean,
    revealedValue: String?,
    onDismiss: () -> Unit,
    onReveal: (() -> Unit)? = null,
    onRemove: (() -> Unit)? = null,
    onSave: (String, String, String, List<String>, Boolean, String) -> Unit,
) {
    var name by rememberSaveable { mutableStateOf(existing?.name.orEmpty()) }
    var value by rememberSaveable { mutableStateOf("") }
    var note by rememberSaveable { mutableStateOf(existing?.note.orEmpty()) }
    var sites by rememberSaveable { mutableStateOf(existing?.sites.orEmpty().joinToString(", ")) }
    var allow by rememberSaveable { mutableStateOf(existing?.allow.orEmpty().toSet()) }
    var reveal by rememberSaveable { mutableStateOf(existing?.reveal != "never") }
    var removing by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val activity = remember(context) { context.asFragmentActivity() }
    val biometric = remember(context) { Biometric.isAvailable(context) }
    val promptTitle = stringResource(R.string.secrets_reveal_prompt)
    val promptSubtitle = stringResource(R.string.lock_biometric_subtitle)
    val cancel = stringResource(R.string.action_cancel)
    val clipboard = LocalClipboard.current
    val snackbar = LocalSnackbar.current
    val copied = stringResource(R.string.secrets_copied)
    val scope = androidx.compose.runtime.rememberCoroutineScope()

    if (removing && onRemove != null) {
        ConfirmDialog(
            stringResource(R.string.secrets_remove_title), stringResource(R.string.secrets_remove_text, existing?.name.orEmpty()),
            stringResource(R.string.secrets_remove), onConfirm = { removing = false; onRemove() }, onDismiss = { removing = false }, destructive = true,
        )
    }
    SenaSheet(stringResource(if (existing == null) R.string.secrets_add else R.string.secrets_edit), onDismiss) {
        SenaField(
            name, { name = it }, Modifier.fillMaxWidth().testTag("secret_name"), label = stringResource(R.string.secrets_name),
            placeholder = stringResource(R.string.secrets_name_hint), singleLine = true, enabled = existing == null,
        )
        SenaField(
            value, { value = it }, Modifier.fillMaxWidth().testTag("secret_value"), label = stringResource(R.string.secrets_value),
            placeholder = stringResource(if (existing == null) R.string.secrets_value_hint_new else R.string.secrets_value_hint_edit),
            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), visualTransformation = PasswordVisualTransformation(),
        )
        SenaField(note, { note = it }, Modifier.fillMaxWidth().testTag("secret_note"), label = stringResource(R.string.secrets_note), placeholder = stringResource(R.string.secrets_note_hint), singleLine = true)
        SenaField(sites, { sites = it }, Modifier.fillMaxWidth().testTag("secret_sites"), label = stringResource(R.string.secrets_sites), placeholder = stringResource(R.string.secrets_sites_hint), singleLine = true)
        Text(stringResource(R.string.secrets_allow), style = MaterialTheme.typography.titleMedium)
        agents.forEach { agent ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(agentLabel(agent), Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
                SenaSwitch(agent in allow, { on -> allow = if (on) allow + agent else allow - agent }, Modifier.testTag("secret_allow_$agent"))
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.secrets_reveal_rule), style = MaterialTheme.typography.bodyLarge)
                Muted(stringResource(R.string.secrets_reveal_rule_hint))
            }
            SenaSwitch(reveal, { reveal = it }, Modifier.testTag("secret_reveal_rule"))
        }
        if (revealedValue != null) {
            SenaCard(Modifier.fillMaxWidth(), padding = Space.inset) {
                SelectionContainer { Text(revealedValue, Modifier.testTag("secret_revealed"), style = MaterialTheme.typography.bodyLarge) }
                SenaButton(stringResource(R.string.secrets_copy), {
                    scope.launch {
                        clipboard.setClipEntry(ClipEntry(sensitiveClip(revealedValue)))
                        snackbar.showSnackbar(copied)
                    }
                }, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
            }
        }
        SenaButton(
            stringResource(R.string.action_save), { onSave(name, value, note, allow.toList(), reveal, sites) }, Modifier.fillMaxWidth().testTag("secret_save"),
            enabled = canSaveSecret(name, value, editing = existing != null), busy = saving, large = true,
        )
        if (onReveal != null && existing?.reveal != "never" && revealedValue == null) {
            SenaButton(
                stringResource(R.string.secrets_show),
                { if (biometric && activity != null) Biometric.prompt(activity, promptTitle, promptSubtitle, cancel, onReveal) else onReveal() },
                Modifier.fillMaxWidth().testTag("secret_show"), style = ButtonStyle.Outline,
            )
        }
        if (onRemove != null) SenaButton(stringResource(R.string.secrets_remove), { removing = true }, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
    }
}

/** A copied password is marked sensitive: Android 13 and newer then don't show it in the clipboard's preview. */
private fun sensitiveClip(value: String): ClipData = ClipData.newPlainText("Sena", value).apply {
    // the extra's own name (ClipDescription.EXTRA_IS_SENSITIVE): older Android versions simply ignore it
    description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
}
