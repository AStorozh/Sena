package com.tema.ia.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import com.tema.ia.R
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.ui.LocalOpenConnect
import com.tema.ia.ui.asString
import com.tema.ia.ui.toUiText
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.OnPastel
import com.tema.ia.ui.theme.Radius
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space

/** First load: pale cards with a running shimmer stand in for the future list. */
@Composable
fun LoadingBox(modifier: Modifier = Modifier) {
    SkeletonList(modifier)
}

/** Empty: a pastel icon, one phrase about "what goes here", and an action if needed. */
@Composable
fun EmptyState(
    @DrawableRes icon: Int,
    text: String,
    modifier: Modifier = Modifier,
    hint: String? = null,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    val c = SenaTheme.colors
    Column(
        modifier.fillMaxSize().rise(key = text).padding(Space.big),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Space.gap, Alignment.CenterVertically),
    ) {
        IconTile(icon, c.tint(icon), size = Size.tileBig)
        Text(text, style = MaterialTheme.typography.titleMedium, color = c.text, textAlign = TextAlign.Center)
        if (hint != null) Text(hint, style = MaterialTheme.typography.bodyMedium, color = c.textMuted, textAlign = TextAlign.Center)
        if (actionLabel != null && onAction != null) SenaButton(actionLabel, onAction)
    }
}

/** Error: what happened, plus "Retry". */
@Composable
fun ErrorState(text: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val c = SenaTheme.colors
    Column(
        modifier.fillMaxSize().rise(key = text).padding(Space.big),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Space.gap, Alignment.CenterVertically),
    ) {
        IconTile(R.drawable.ic_wifi_off, Coral, size = Size.tileBig)
        Text(text, style = MaterialTheme.typography.bodyLarge, color = c.text, textAlign = TextAlign.Center)
        SenaButton(stringResource(R.string.action_retry), onRetry, icon = R.drawable.ic_refresh)
    }
}

/**
 * A bar at the top of the sheet: the last refresh failed, the data below is the last that came. The words say why: no connection,
 * a refused password (the bar then opens the connection screen), a locked address, or the server's own explanation.
 */
@Composable
fun ProblemBanner(problem: Throwable, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val kind = (problem as? ApiException)?.kind
    val openConnect = LocalOpenConnect.current
    val connect = kind == ApiError.BadToken && openConnect != null
    Surface(
        onClick = openConnect.takeIf { connect } ?: onRetry,
        modifier = modifier.fillMaxWidth().padding(horizontal = Space.screen).padding(top = Space.gap).heightIn(min = Size.touch).testTag("problem_banner"),
        shape = Radius.pill,
        color = Coral,
        contentColor = OnPastel,
    ) {
        Row(
            Modifier.padding(horizontal = Space.inset, vertical = Space.small),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Space.small),
        ) {
            SenaIcon(if (kind == ApiError.NoServer) R.drawable.ic_wifi_off else R.drawable.ic_alert, null, size = Size.iconSmall)
            Text(
                when (kind) {
                    ApiError.NoServer -> stringResource(R.string.offline_banner)
                    ApiError.BadToken -> stringResource(R.string.banner_bad_token)
                    else -> problem.toUiText().asString()
                },
                style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
            Text(stringResource(if (connect) R.string.settings_change else R.string.action_retry), style = MaterialTheme.typography.labelLarge)
        }
    }
}
