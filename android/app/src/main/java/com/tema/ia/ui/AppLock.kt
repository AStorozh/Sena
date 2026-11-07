package com.tema.ia.ui

import android.os.SystemClock
import androidx.activity.compose.LocalActivity
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.tema.ia.R
import com.tema.ia.data.PinCheck
import com.tema.ia.data.Store
import com.tema.ia.ui.Biometric.asFragmentActivity
import com.tema.ia.ui.components.SenaIcon
import com.tema.ia.ui.components.SenaLeaf
import com.tema.ia.ui.components.pressScale
import com.tema.ia.ui.components.rememberPress
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Motion
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Size
import com.tema.ia.ui.theme.Space
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

private enum class Phase { Loading, Setup, Locked, Welcome, Content }

/** Whether a lifecycle event closes the lock: going to the background does; the activity rebuilt for a rotation, a theme or a font change doesn't. */
internal fun closesLock(event: Lifecycle.Event, changingConfigurations: Boolean): Boolean =
    event == Lifecycle.Event.ON_STOP && !changingConfigurations

/** The lock is closed over the app: the screens under it are kept, but their polling waits (PollWhileVisible). */
val LocalLocked = staticCompositionLocalOf { false }

/**
 * Gates the whole app behind a 4-digit code the owner sets up the first time, so a stranger who picks up the phone
 * can't open Sena. Backgrounding the app (ON_STOP) re-locks it. Order: enter/create the code, a short welcome with
 * the leaf, then the app itself. [unlocked] — whether the lock is open, for the whole process (AppContainer.unlocked).
 *
 * Once opened, the app stays composed under the closed lock — not drawn, not touchable, not read aloud — so an app switch or
 * the screen going dark doesn't send him back to Home with his half-typed message and the answer he's waiting for gone (the
 * history hides a message not answered yet: he would send it again, a second paid run). What runs stops with the lock: the
 * screens' polling waits for it to open, the live voice ends (VoiceViewModel), a chat answer's watch pauses (ChatViewModel).
 */
@Composable
fun AppLockGate(store: Store, unlocked: MutableStateFlow<Boolean>, content: @Composable () -> Unit) {
    var phase by remember { mutableStateOf(if (unlocked.value) Phase.Content else Phase.Loading) }
    val open by unlocked.collectAsState()
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    var opened by remember { mutableStateOf(phase == Phase.Content) }   // the app was let in at least once in this process: it stays composed

    val biometricOn by store.biometricEnabled.collectAsState(false)
    val activity = LocalContext.current.asFragmentActivity()
    val host = LocalActivity.current
    val biometricTitle = stringResource(R.string.lock_biometric_title)
    val biometricSubtitle = stringResource(R.string.lock_biometric_subtitle)
    val biometricCancel = stringResource(R.string.lock_biometric_cancel)
    val biometricPassed = { scope.launch { store.clearPinTries() }; phase = Phase.Welcome }
    // Offered once per time the screen locks — ON_RESUME, not composition, because the prompt only reliably
    // takes the screen once the window actually has focus again (right after composing it, coming back from
    // the background, it can silently do nothing).
    var biometricShown by remember { mutableStateOf(false) }
    val offerBiometric = {
        if (!biometricShown && biometricOn && activity != null && Biometric.isAvailable(activity)) {
            biometricShown = true
            Biometric.prompt(activity, biometricTitle, biometricSubtitle, biometricCancel) { biometricPassed() }
        }
    }

    LaunchedEffect(Unit) {
        if (phase == Phase.Loading) {
            phase = if (store.hasPin()) Phase.Locked else Phase.Setup
            if (phase == Phase.Locked) offerBiometric()
        }
    }
    LaunchedEffect(open) { if (open && phase != Phase.Content) phase = Phase.Content }   // opened from outside (on-device tests)

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when {
                phase == Phase.Content && closesLock(event, host?.isChangingConfigurations == true) -> {
                    unlocked.value = false
                    phase = Phase.Locked
                    biometricShown = false
                    focus.clearFocus(force = true)   // no field under the lock keeps the keyboard: nothing can be typed into the hidden app
                }
                event == Lifecycle.Event.ON_RESUME && phase == Phase.Locked -> offerBiometric()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(phase) { if (phase == Phase.Content) opened = true }

    Box(Modifier.fillMaxSize()) {
        if (opened || phase == Phase.Content) {
            val hidden = phase != Phase.Content
            Box(Modifier.fillMaxSize().then(if (hidden) Modifier.hiddenUnderLock() else Modifier)) {
                CompositionLocalProvider(LocalLocked provides hidden, content = content)
            }
        }
        when (phase) {
            Phase.Loading -> Box(Modifier.fillMaxSize().background(SenaTheme.colors.header))
            Phase.Setup -> SetupScreen { pin -> scope.launch { store.setPin(pin) }; phase = Phase.Welcome }
            Phase.Locked -> LockScreen(
                biometric = if (biometricOn && activity != null && Biometric.isAvailable(activity)) activity else null,
                onBiometricDone = { biometricPassed() },
            ) { pin, onResult ->
                scope.launch {
                    val check = store.checkPin(pin)
                    if (check == PinCheck.Right) phase = Phase.Welcome else onResult(check)
                }
            }
            Phase.Welcome -> WelcomeScreen { unlocked.value = true; phase = Phase.Content }
            Phase.Content -> Unit
        }
    }
}

/**
 * The app under the closed lock: still composed (its screens keep what was typed and what is running), but measured and never
 * placed — so nothing of it is drawn, hit by a touch or read by TalkBack (its semantics are cleared too).
 */
private fun Modifier.hiddenUnderLock(): Modifier = clearAndSetSemantics {}.layout { measurable, constraints ->
    val placeable = measurable.measure(constraints)
    layout(placeable.width, placeable.height) {}
}

@Composable
private fun SetupScreen(onDone: (String) -> Unit) {
    var step by remember { mutableIntStateOf(0) }
    var first by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var mismatch by remember { mutableStateOf(false) }

    fun submit(value: String) {
        if (step == 0) {
            first = value; pin = ""; step = 1
        } else if (value == first) {
            onDone(value)
        } else {
            mismatch = true; pin = ""; first = ""; step = 0
        }
    }

    PinScaffold(
        title = stringResource(if (step == 0) R.string.lock_setup_title else R.string.lock_setup_confirm_title),
        subtitle = if (mismatch) stringResource(R.string.lock_setup_mismatch) else if (step == 0) stringResource(R.string.lock_setup_subtitle) else null,
        error = mismatch,
        length = pin.length,
        onDigit = { d -> if (pin.length < 4) { mismatch = false; val next = pin + d; pin = next; if (next.length == 4) submit(next) } },
        onBackspace = { if (pin.isNotEmpty()) pin = pin.dropLast(1) },
    )
}

@Composable
private fun LockScreen(
    biometric: FragmentActivity?,
    onBiometricDone: () -> Unit,
    onSubmit: (pin: String, onResult: (PinCheck) -> Unit) -> Unit,
) {
    var pin by remember { mutableStateOf("") }
    var wrong by remember { mutableStateOf(false) }
    var waitUntil by remember { mutableLongStateOf(0L) }   // after too many wrong codes: no tries before this (the phone's uptime clock)
    var waitSeconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(waitUntil) {
        while (true) {
            val left = waitUntil - SystemClock.elapsedRealtime()
            waitSeconds = if (left > 0) ((left + 999) / 1000).toInt() else 0
            if (left <= 0) break
            delay(minOf(left, 1000L))
        }
    }
    val title = stringResource(R.string.lock_biometric_title)
    val subtitle = stringResource(R.string.lock_biometric_subtitle)
    val cancel = stringResource(R.string.lock_biometric_cancel)
    // The owner's own retry (AppLockGate already offers it once by itself when the screen locks): tapping
    // the fingerprint button always asks again, even if the first, automatic offer was missed or dismissed.
    val askBiometric = {
        if (biometric != null) Biometric.prompt(biometric, title, subtitle, cancel, onBiometricDone)
    }
    PinScaffold(
        title = stringResource(R.string.lock_title),
        subtitle = when {
            waitSeconds > 0 -> stringResource(R.string.lock_wait, waitSeconds)
            wrong -> stringResource(R.string.lock_pin_wrong)
            else -> stringResource(R.string.lock_subtitle)
        },
        error = wrong || waitSeconds > 0,
        length = pin.length,
        onDigit = { d ->
            if (pin.length < 4 && waitSeconds == 0) {
                wrong = false
                val next = pin + d
                pin = next
                if (next.length == 4) onSubmit(next) { check ->
                    pin = ""
                    wrong = check == PinCheck.Wrong
                    if (check is PinCheck.Wait) waitUntil = SystemClock.elapsedRealtime() + check.ms
                }
            }
        },
        onBackspace = { if (pin.isNotEmpty()) pin = pin.dropLast(1) },
        onBiometric = if (biometric != null) askBiometric else null,
    )
}

@Composable
private fun PinScaffold(
    title: String,
    subtitle: String?,
    error: Boolean,
    length: Int,
    onDigit: (Int) -> Unit,
    onBackspace: () -> Unit,
    onBiometric: (() -> Unit)? = null,
) {
    SecureWindow()   // the code being typed stays out of screen recordings
    val c = SenaTheme.colors
    Box(Modifier.fillMaxSize().background(c.header), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.section)) {
            SenaLeaf(size = Size.leafTop, tint = c.onHeader, description = stringResource(R.string.cd_leaf))
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.small)) {
                Text(title, style = MaterialTheme.typography.titleLarge, color = c.onHeader)
                if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyLarge, color = if (error) Coral else c.onHeaderMuted)
            }
            PinDots(length, error)
            PinPad(onDigit, onBackspace, onBiometric)
        }
    }
}

@Composable
private fun PinDots(length: Int, error: Boolean) {
    val c = SenaTheme.colors
    Row(horizontalArrangement = Arrangement.spacedBy(Space.gap)) {
        repeat(4) { i ->
            val filled = i < length
            Box(
                Modifier.size(Size.dotBig).background(
                    when {
                        error -> Coral
                        filled -> c.onHeader
                        else -> c.onHeader.copy(alpha = 0.25f)
                    },
                    CircleShape,
                ),
            )
        }
    }
}

@Composable
private fun PinPad(onDigit: (Int) -> Unit, onBackspace: () -> Unit, onBiometric: (() -> Unit)? = null) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.gap)) {
        listOf(listOf(1, 2, 3), listOf(4, 5, 6), listOf(7, 8, 9)).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(Space.gap)) { row.forEach { d -> PadDigit(d) { onDigit(d) } } }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Space.gap), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(Size.pad), contentAlignment = Alignment.Center) {
                if (onBiometric != null) PadAction(R.drawable.ic_fingerprint, stringResource(R.string.lock_biometric_cd), onBiometric)
            }
            PadDigit(0) { onDigit(0) }
            Box(Modifier.size(Size.pad), contentAlignment = Alignment.Center) {
                PadAction(R.drawable.ic_backspace, stringResource(R.string.lock_backspace_cd), onBackspace)
            }
        }
    }
}

@Composable
private fun PadDigit(digit: Int, onClick: () -> Unit) {
    val c = SenaTheme.colors
    val press = rememberPress()
    Surface(
        onClick = onClick,
        modifier = Modifier.size(Size.pad).pressScale(press),
        shape = CircleShape,
        color = c.onHeader.copy(alpha = 0.08f),
        contentColor = c.onHeader,
        interactionSource = press,
    ) {
        Box(contentAlignment = Alignment.Center) { Text(digit.toString(), style = MaterialTheme.typography.headlineMedium) }
    }
}

@Composable
private fun PadAction(icon: Int, description: String, onClick: () -> Unit) {
    val c = SenaTheme.colors
    val press = rememberPress()
    Surface(
        onClick = onClick,
        modifier = Modifier.size(Size.pad).pressScale(press),
        shape = CircleShape,
        color = c.onHeader.copy(alpha = 0.08f),
        contentColor = c.onHeader,
        interactionSource = press,
    ) {
        Box(contentAlignment = Alignment.Center) { SenaIcon(icon, description, size = Size.icon) }
    }
}

/** A short, visible "welcome" after unlocking: the leaf grows in and fades in. A fresh [Animatable] every time this
 * screen is entered, so it plays in full on every single unlock — not just the first one in the process's lifetime. */
@Composable
private fun WelcomeScreen(onDone: () -> Unit) {
    val c = SenaTheme.colors
    val progress = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        progress.animateTo(1f, tween(Motion.rise, easing = FastOutSlowInEasing))
        delay(350)
        onDone()
    }
    Box(Modifier.fillMaxSize().background(c.header), contentAlignment = Alignment.Center) {
        SenaLeaf(
            size = Size.leafBig, tint = c.onHeader, description = stringResource(R.string.cd_leaf),
            modifier = Modifier.graphicsLayer {
                alpha = progress.value
                val scale = 0.6f + 0.4f * progress.value
                scaleX = scale
                scaleY = scale
            },
        )
    }
}
