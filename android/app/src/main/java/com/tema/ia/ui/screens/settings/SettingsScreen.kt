package com.tema.ia.ui.screens.settings

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.BuildConfig
import com.tema.ia.R
import com.tema.ia.data.ConfigInfo
import com.tema.ia.data.ThemeMode
import com.tema.ia.ui.Biometric
import com.tema.ia.ui.Biometric.asFragmentActivity
import com.tema.ia.ui.CollectMessages
import com.tema.ia.ui.Fmt
import com.tema.ia.ui.Load
import com.tema.ia.ui.PollWhileVisible
import com.tema.ia.ui.components.ButtonRow
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.ConfirmDialog
import com.tema.ia.ui.components.LeadCard
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SegmentedControl
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaCard
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.SenaSwitch
import com.tema.ia.ui.components.Spinner
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.problem
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.valueOrNull

@Composable
fun SettingsScreen(viewModel: SettingsViewModel, demo: Boolean, address: String, onChangeConnection: () -> Unit, onBack: () -> Unit) {
    val load by viewModel.loader.state.collectAsStateWithLifecycle()
    val theme by viewModel.theme.collectAsStateWithLifecycle()
    val confirm by viewModel.confirmDisconnect.collectAsStateWithLifecycle()
    PollWhileVisible(viewModel.loader)
    CollectMessages(viewModel.messages)

    if (confirm) {
        ConfirmDialog(stringResource(R.string.settings_disconnect_title), stringResource(R.string.settings_disconnect_text), stringResource(R.string.settings_disconnect),
            onConfirm = viewModel::disconnect, onDismiss = viewModel::dismissDisconnect, destructive = true)
    }

    // turning Sena on and off has one place, Home (Ф-8): there it waits for the server to confirm and says "Starting…"
    SenaScreen(
        title = stringResource(R.string.settings_title),
        onBack = onBack,
        problem = load.problem, onRetry = viewModel.loader::refresh,
    ) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current).testTag("settings_list"),
            verticalArrangement = Arrangement.spacedBy(Space.gap),
        ) {
            LeadCard(Modifier.fillMaxWidth().rise(0, "set-conn")) {
                Text(stringResource(R.string.settings_connection), style = MaterialTheme.typography.titleLarge)
                Muted(if (demo) stringResource(R.string.settings_demo) else stringResource(R.string.settings_connected_to, address), style = MaterialTheme.typography.bodyLarge)
                ButtonRow {
                    if (demo) {
                        SenaButton(stringResource(R.string.settings_connect_mac), viewModel::disconnect, Modifier.testTag("settings_connect"))
                    } else {
                        SenaButton(stringResource(R.string.settings_change), onChangeConnection, Modifier.testTag("settings_change"))
                        SenaButton(stringResource(R.string.settings_disconnect), viewModel::askDisconnect, Modifier.testTag("settings_disconnect"), style = ButtonStyle.Outline)
                    }
                }
            }

            SenaCard(Modifier.fillMaxWidth().rise(1, "set-look")) {
                Text(stringResource(R.string.settings_look), style = MaterialTheme.typography.titleLarge)
                Choice(
                    listOf(ThemeMode.System to R.string.settings_theme_system, ThemeMode.Dark to R.string.settings_theme_dark, ThemeMode.Light to R.string.settings_theme_light),
                    theme, viewModel::setTheme, "theme",
                )
            }

            val context = LocalContext.current
            val biometricActivity = remember(context) { context.asFragmentActivity() }
            val biometricAvailable = remember(context) { Biometric.isAvailable(context) }
            if (biometricAvailable) {
                val biometricOn by viewModel.biometricEnabled.collectAsStateWithLifecycle()
                val confirmTitle = stringResource(R.string.settings_biometric_confirm_title)
                val subtitle = stringResource(R.string.lock_biometric_subtitle)
                val cancel = stringResource(R.string.action_cancel)
                SenaCard(Modifier.fillMaxWidth().rise(2, "set-security")) {
                    Text(stringResource(R.string.settings_security), style = MaterialTheme.typography.titleLarge)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(stringResource(R.string.settings_biometric), style = MaterialTheme.typography.bodyLarge)
                            Muted(stringResource(R.string.settings_biometric_hint))
                        }
                        SenaSwitch(
                            biometricOn,
                            { checked ->
                                if (!checked) {
                                    viewModel.setBiometricEnabled(false)
                                } else if (biometricActivity != null) {
                                    Biometric.prompt(biometricActivity, confirmTitle, subtitle, cancel) { viewModel.setBiometricEnabled(true) }
                                }
                            },
                            Modifier.testTag("switch_biometric"),
                        )
                    }
                }
            }

            SenaCard(Modifier.fillMaxWidth().rise(3, "set-beh").animateContentSize()) {
                Text(stringResource(R.string.settings_behavior), style = MaterialTheme.typography.titleLarge)
                when (val l = load) {
                    is Load.Loading -> Spinner(color = SenaTheme.colors.text)
                    is Load.Failed -> Muted(stringResource(R.string.settings_behavior_unavailable))
                    is Load.Ready -> Behavior(l.value, viewModel)
                }
            }

            SenaCard(Modifier.fillMaxWidth().rise(4, "set-about")) {
                Text(stringResource(R.string.settings_about), style = MaterialTheme.typography.titleLarge)
                Muted(stringResource(R.string.settings_version, BuildConfig.VERSION_NAME), style = MaterialTheme.typography.bodyLarge)
                (load as? Load.Ready)?.value?.commit?.let {
                    Muted(stringResource(R.string.settings_sena_version, it), style = MaterialTheme.typography.bodyLarge)
                }
            }
        }
    }
}

@Composable
private fun <T> Choice(options: List<Pair<T, Int>>, selected: T, onSelect: (T) -> Unit, tag: String) {
    val labels = options.map { stringResource(it.second) }
    SegmentedControl(labels, options.indexOfFirst { it.first == selected }, { onSelect(options[it].first) }, Modifier.testTag("choice_$tag"))
}

/**
 * The server's own choices: the app words the ones it knows in its language; one it doesn't know yet (a newer server) is shown
 * with the server's own label, so it can still be picked.
 */
@Composable
private fun ServerChoice(options: Map<String, String>, known: Map<String, Int>, selected: String, onSelect: (String) -> Unit, tag: String) {
    val values = options.keys.toList().ifEmpty { known.keys.toList() }
    val labels = values.map { v -> known[v]?.let { stringResource(it) } ?: options[v].orEmpty().ifBlank { v } }
    SegmentedControl(labels, values.indexOf(selected), { onSelect(values[it]) }, Modifier.testTag("choice_$tag"))
}

@Composable
private fun Behavior(config: ConfigInfo, vm: SettingsViewModel) {
    val s = config.settings
    val verbosity = mapOf("low" to R.string.opt_verbosity_low, "medium" to R.string.opt_verbosity_medium)
    val effort = mapOf("low" to R.string.opt_effort_low, "medium" to R.string.opt_effort_medium)
    Muted(stringResource(R.string.settings_verbosity), style = MaterialTheme.typography.labelLarge)
    ServerChoice(config.verbosityOptions, verbosity, s.verbosity, { vm.change("verbosity", it) }, "verbosity")
    Muted(stringResource(R.string.settings_effort), style = MaterialTheme.typography.labelLarge)
    ServerChoice(config.effortOptions, effort, s.effort, { vm.change("effort", it) }, "effort")
    Budget(s.budgetUsdDay, config.budgetMax, R.string.settings_budget, R.string.settings_budget_hint, "budget_field") { vm.change("budget_usd_day", it) }
    // one task's own brake (the brain sums up and stops past it): without a field here it could only be changed in the settings file
    Budget(s.taskBudgetUsd, config.taskBudgetMax, R.string.settings_task_budget, R.string.settings_task_budget_hint, "task_budget_field") { vm.change("task_budget_usd", it) }
    SwitchRow(R.string.settings_heartbeat, s.heartbeat, "heartbeat") { vm.change("heartbeat", it) }
    SwitchRow(R.string.settings_tidy, s.tidy, "tidy") { vm.change("tidy", it) }
    SwitchRow(R.string.settings_weekly, s.weekly, "weekly") { vm.change("weekly", it) }
    SwitchRow(R.string.settings_voice_reply, s.voiceReply, "voice_reply") { vm.change("voice_reply", it) }
    SwitchRow(R.string.settings_telegram_notify, s.telegramNotify, "telegram_notify") { vm.change("telegram_notify", it) }
}

/** Dollars (a day, or for one task), cents allowed ("2.5"): the server takes fractions; the field accepts a dot or a comma. */
@Composable
private fun Budget(current: Double, max: Int, label: Int, hint: Int, tag: String, onSave: (Double) -> Unit) {
    var text by remember(current) { mutableStateOf(Fmt.number(current)) }
    val save = { budgetOf(text)?.coerceIn(0.0, max.toDouble())?.let { if (it != current) onSave(it) } }
    SenaField(
        text, { text = it.filter { c -> c.isDigit() || c == '.' || c == ',' }.take(8) }, Modifier.fillMaxWidth().testTag(tag),
        label = stringResource(label), supporting = stringResource(hint), singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { save() }),
    )
}

@Composable
private fun SwitchRow(label: Int, checked: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    val text = stringResource(label)
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = text }, verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        SenaSwitch(checked, onChange, Modifier.testTag("switch_$tag"))
    }
}
