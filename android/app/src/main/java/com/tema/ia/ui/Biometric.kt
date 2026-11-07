package com.tema.ia.ui

import android.content.Context
import android.content.ContextWrapper
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/** Fingerprint/face unlock: a faster way into the same lock, never a replacement for the PIN (the system keeps
 * the PIN as its own fallback if the owner has one set in Android itself; Sena's PIN pad is always underneath). */
object Biometric {
    fun isAvailable(context: Context): Boolean =
        BiometricManager.from(context).canAuthenticate(BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS

    /** The nearest FragmentActivity behind whatever Context Compose handed us (it's sometimes wrapped). */
    fun Context.asFragmentActivity(): FragmentActivity? {
        var ctx = this
        while (ctx is ContextWrapper) {
            if (ctx is FragmentActivity) return ctx
            ctx = ctx.baseContext
        }
        return null
    }

    fun prompt(activity: FragmentActivity, title: String, subtitle: String, cancel: String, onSuccess: () -> Unit) {
        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
            // A wrong finger/face: the system dialog already says so and lets the owner try again — nothing for us to do.
            // A real error (cancelled, too many attempts, no sensor right now): the PIN pad underneath still works.
        }
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title).setSubtitle(subtitle).setNegativeButtonText(cancel)
            .setAllowedAuthenticators(BIOMETRIC_STRONG)
            .build()
        BiometricPrompt(activity, ContextCompat.getMainExecutor(activity), callback).authenticate(info)
    }
}
