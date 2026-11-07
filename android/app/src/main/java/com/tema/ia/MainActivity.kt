package com.tema.ia

import android.graphics.Color
import android.os.Bundle
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.fragment.app.FragmentActivity
import com.tema.ia.ui.SenaRoot

// FragmentActivity (not plain ComponentActivity): BiometricPrompt needs a host that can carry
// its headless result-fragment across configuration changes.
class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Android 12+ shows this regardless; no keep-on-screen condition, so it clears the instant the first frame is ready —
        // same background and a transparent icon make it look like the lock screen was there from the start.
        installSplashScreen()
        super.onCreate(savedInstanceState)
        val container = (application as SenaApplication).container
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT))
        setContent {
            SenaRoot(container) { dark ->
                // System bars are transparent. The top header is always dark, so status icons are light; the bottom sheet's color follows the app's theme, not the phone's
                val bottom = if (dark) SystemBarStyle.dark(Color.TRANSPARENT) else SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT), navigationBarStyle = bottom)
            }
        }
    }
}
