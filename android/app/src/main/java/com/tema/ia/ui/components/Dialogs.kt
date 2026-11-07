package com.tema.ia.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import com.tema.ia.R
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** Confirmation of an irreversible action: what will happen, "Cancel" on the left, action on the right (coral if destructive). */
@Composable
fun ConfirmDialog(
    title: String,
    text: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    destructive: Boolean = false,
) {
    val c = SenaTheme.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        shape = Radius.card,
        title = { Text(title, style = MaterialTheme.typography.titleLarge, color = c.text) },
        text = { Text(text, style = MaterialTheme.typography.bodyLarge, color = c.textMuted) },
        dismissButton = { SenaButton(stringResource(R.string.action_cancel), onDismiss, style = ButtonStyle.Text) },
        confirmButton = { SenaButton(confirmLabel, onConfirm, style = if (destructive) ButtonStyle.Danger else ButtonStyle.Primary) },
    )
}

/** Bottom sheet: a title and content on a light sheet, with one field and one button inside. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SenaSheet(title: String, onDismiss: () -> Unit, content: @Composable () -> Unit) {
    val c = SenaTheme.colors
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = c.sheet,
        contentColor = c.text,
        shape = Radius.sheet,
        dragHandle = {
            Box(Modifier.padding(vertical = Space.gap).size(Size.handleWidth, Size.handleHeight).background(c.outline, Radius.pill).clearAndSetSemantics { })
        },
    ) {
        Column(
            Modifier.fillMaxWidth().imePadding().navigationBarsPadding().verticalScroll(rememberScrollState()).padding(horizontal = Space.screen).padding(bottom = Space.screen),
            verticalArrangement = Arrangement.spacedBy(Space.gap),
        ) {
            Text(title, style = MaterialTheme.typography.headlineSmall, color = c.text)
            content()
        }
    }
}
