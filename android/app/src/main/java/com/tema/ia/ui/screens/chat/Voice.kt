package com.tema.ia.ui.screens.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tema.ia.R
import com.tema.ia.data.AndroidMic
import com.tema.ia.data.ApiError
import com.tema.ia.data.AndroidSpeaker
import com.tema.ia.data.LiveVoice
import com.tema.ia.data.Mic
import com.tema.ia.data.SenaApi
import com.tema.ia.data.Speaker
import com.tema.ia.data.VoicePhase
import com.tema.ia.data.VoiceState
import com.tema.ia.ui.asString
import com.tema.ia.ui.components.ButtonStyle
import com.tema.ia.ui.components.Dot
import com.tema.ia.ui.components.Muted
import com.tema.ia.ui.components.SenaButton
import com.tema.ia.ui.components.SenaSheet
import com.tema.ia.ui.components.TypingDots
import com.tema.ia.ui.theme.Coral
import com.tema.ia.ui.theme.Lime
import com.tema.ia.ui.theme.SenaTheme
import com.tema.ia.ui.theme.Sky
import com.tema.ia.ui.theme.Space
import com.tema.ia.ui.toUiText
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The live voice in the chat: the microphone button starts a conversation with Sena by voice (no name needed: the button is
 * the call), she answers in her own voice in about a second; the sheet shows what she heard and said. The microphone runs only
 * while the sheet is open — no background listening on the phone: when the app goes to the background its lock closes ([open]
 * turns false) and the conversation ends with it; a cleared ViewModel (another connection) ends it too.
 * [audio] makes the phone's microphone and speaker (on the phone: [AndroidMic] and [AndroidSpeaker]).
 */
class VoiceViewModel(
    private val api: SenaApi,
    private val open: StateFlow<Boolean> = MutableStateFlow(true),   // the app's lock is open (AppContainer.unlocked)
    private val audio: () -> Pair<Mic, Speaker>,
) : ViewModel() {
    private var talk: LiveVoice? = null
    private var watch: Job? = null
    private val _state = MutableStateFlow<VoiceState?>(null)   // null — no conversation, no sheet
    val state: StateFlow<VoiceState?> = _state.asStateFlow()

    init {
        viewModelScope.launch { open.collect { if (!it) close() } }   // Ф-41: the lock closed — nothing of the conversation stays on
    }

    /** Whether this connection has a live voice at all (the demo and an https address don't). */
    val available: Boolean get() = api.voiceUrl() != null

    fun start() {
        val url = api.voiceUrl() ?: return
        release()
        // a speaker the phone can't open right now is a failed conversation in words, not a crash
        val (mic, speaker) = runCatching { audio() }.getOrElse {
            _state.value = VoiceState(phase = VoicePhase.Failed, speakerFailed = true)
            return
        }
        val conversation = LiveVoice(url, mic, speaker, headers = api.voiceHeaders())
        talk = conversation
        watch = viewModelScope.launch {
            var asked = false
            conversation.state.collect { s ->
                _state.value = s
                // nothing answered at the voice's port: the Mac may well be there with Sena herself off (the screen alone doesn't
                // listen for the voice) — then that is what he hears, not "the Mac isn't answering"
                if (s.phase == VoicePhase.Failed && s.error?.kind == ApiError.NoServer && !asked) {
                    asked = true
                    if (runCatching { !api.overview().coreAlive }.getOrDefault(false)) _state.value = s.copy(senaOff = true)
                }
            }
        }
        conversation.start()
    }

    /** "Finish": she stops at once; the sheet stays with what was said. */
    fun end() {
        talk?.stop()
    }

    /** The sheet is closed: nothing of the conversation keeps running. */
    fun close() {
        release()
        _state.value = null
    }

    private fun release() {
        watch?.cancel()
        watch = null
        talk?.release()
        talk = null
    }

    override fun onCleared() = release()
}

/** The conversation's sheet: where it is (with a dot and a word), what Sena heard and said, "Finish" or "Talk again". */
@Composable
fun VoiceSheet(state: VoiceState, onEnd: () -> Unit, onAgain: () -> Unit, onClose: () -> Unit) {
    val colors = SenaTheme.colors
    val active = state.phase in setOf(VoicePhase.Connecting, VoicePhase.Listening, VoicePhase.Thinking)
    // a conversation doesn't touch the screen: without this the phone's own timeout dims it, the app stops and the call ends
    val view = LocalView.current
    DisposableEffect(view, active) {
        if (active) view.keepScreenOn = true
        onDispose { if (active) view.keepScreenOn = false }
    }
    SenaSheet(stringResource(R.string.voice_title), onDismiss = onClose) {
        Row(Modifier.fillMaxWidth().testTag("voice_phase"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Space.small)) {
            if (state.phase == VoicePhase.Thinking) {
                TypingDots(colors.text)
            } else {
                Dot(when (state.phase) { VoicePhase.Listening -> Lime; VoicePhase.Failed -> Coral; VoicePhase.Ended -> colors.outline; else -> Sky })
            }
            Text(
                when (state.phase) {
                    VoicePhase.Connecting -> stringResource(R.string.voice_connecting)
                    VoicePhase.Listening -> stringResource(R.string.voice_listening)
                    VoicePhase.Thinking -> stringResource(R.string.voice_thinking)
                    VoicePhase.Ended -> stringResource(R.string.voice_ended)
                    VoicePhase.Failed -> when {
                        state.micFailed -> stringResource(R.string.voice_mic_failed)
                        state.speakerFailed -> stringResource(R.string.voice_speaker_failed)
                        state.senaOff -> stringResource(R.string.voice_sena_off)
                        else -> state.error?.toUiText()?.asString() ?: stringResource(R.string.err_unknown)
                    }
                },
                style = MaterialTheme.typography.titleMedium,
                color = if (state.phase == VoicePhase.Failed) colors.badText else colors.text,
            )
        }
        val reason = state.reason
        if (state.phase == VoicePhase.Ended && reason != null) Muted(stringResource(R.string.voice_ended_why, reason))
        if (state.heard.isNotBlank()) Line(stringResource(R.string.voice_you), state.heard)
        if (state.said.isNotBlank()) Line(stringResource(R.string.voice_sena), state.said)
        if (active) {
            Muted(stringResource(R.string.voice_hint))
            SenaButton(stringResource(R.string.voice_finish), onEnd, Modifier.fillMaxWidth().testTag("voice_finish"), style = ButtonStyle.Danger, icon = R.drawable.ic_stop)
        } else {
            SenaButton(stringResource(R.string.voice_again), onAgain, Modifier.fillMaxWidth().testTag("voice_again"), icon = R.drawable.ic_mic)
            SenaButton(stringResource(R.string.voice_close), onClose, Modifier.fillMaxWidth(), style = ButtonStyle.Outline)
        }
    }
}

@Composable
private fun Line(who: String, text: String) {
    Column(verticalArrangement = Arrangement.spacedBy(Space.tiny)) {
        Muted(who, style = MaterialTheme.typography.labelMedium)
        Text(text, style = MaterialTheme.typography.bodyLarge, color = SenaTheme.colors.text)
    }
}
