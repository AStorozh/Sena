package com.tema.ia.ui.screens.connect

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tema.ia.R
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.HeaderNote
import com.tema.ia.ui.components.LocalBottomPad
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaField
import com.tema.ia.ui.components.SenaIconButton
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.SenaScreen
import com.tema.ia.ui.components.rise
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** The "Connection" screen. [onBack] is set when it was opened from settings rather than on first launch. */
@Composable
fun ConnectScreen(viewModel: ConnectViewModel, onBack: (() -> Unit)? = null, allowDemo: Boolean = true) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var showToken by rememberSaveable { mutableStateOf(false) }
    val checking = ui.check == CheckState.Checking
    val colors = SenaTheme.colors

    SenaScreen(
        title = stringResource(R.string.connect_title),
        onBack = onBack,
        leading = if (onBack == null) {
            { SenaLeaf(size = Size.leafTop, description = stringResource(R.string.cd_leaf)) }
        } else null,
        header = { HeaderNote(stringResource(R.string.connect_intro)) },
    ) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = Space.screen).padding(top = Space.screen, bottom = LocalBottomPad.current),
            verticalArrangement = Arrangement.spacedBy(Space.gap),
        ) {
            SenaField(
                ui.address, viewModel::onAddress, Modifier.fillMaxWidth().rise(0, "c-addr").testTag("connect_address"),
                label = stringResource(R.string.connect_address), placeholder = stringResource(R.string.connect_address_hint),
                singleLine = true, isError = ui.addressError != null, supporting = ui.addressError?.asString() ?: stringResource(R.string.connect_emulator),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            )
            SenaField(
                ui.token, viewModel::onToken, Modifier.fillMaxWidth().rise(1, "c-token").testTag("connect_token"),
                label = stringResource(R.string.connect_token), singleLine = true, isError = ui.tokenError != null, supporting = ui.tokenError?.asString(),
                visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { viewModel.submit() }),
                trailing = {
                    SenaIconButton(
                        if (showToken) R.drawable.ic_eye_off else R.drawable.ic_eye,
                        stringResource(if (showToken) R.string.cd_hide_password else R.string.cd_show_password),
                        { showToken = !showToken }, tint = colors.text,
                    )
                },
            )
            val failed = ui.check as? CheckState.Failed
            if (failed != null && ui.tokenError == null) {
                Text(failed.text.asString(), style = MaterialTheme.typography.bodyMedium, color = colors.badText, modifier = Modifier.fillMaxWidth().testTag("connect_error"))
            }
            SenaButton(
                stringResource(if (checking) R.string.connect_checking else R.string.connect_check), viewModel::submit,
                Modifier.fillMaxWidth().rise(2, "c-submit").testTag("connect_submit"), busy = checking, large = true,
            )
            if (allowDemo) {
                SenaButton(stringResource(R.string.connect_demo), viewModel::demo, Modifier.fillMaxWidth().rise(3, "c-demo").testTag("connect_demo"), style = ButtonStyle.Outline, enabled = !checking, large = true)
            }
        }
    }
}
