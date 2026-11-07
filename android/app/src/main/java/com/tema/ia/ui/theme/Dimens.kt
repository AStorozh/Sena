package com.tema.ia.ui.theme

import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp

/** Padding: a scale of 4, 8, 12, 16, 20, 24, 32, 48. No other numbers appear in screens. */
object Space {
    val tiny = 4.dp
    val small = 8.dp
    val gap = 12.dp
    val inset = 16.dp
    val screen = 20.dp
    val section = 24.dp
    val big = 32.dp
}

/** Element sizes. */
object Size {
    val touch = 48.dp
    val touchHeader = 34.dp    // icon buttons in the screen header: lighter than the usual touch target
    val tab = 40.dp            // pills in SegmentTabs
    val action = 56.dp
    val icon = 22.dp
    val iconSmall = 18.dp
    val tile = 48.dp           // the icon square in a list row
    val tileBig = 64.dp        // the same square in an empty state and a card header
    val avatar = 40.dp
    val dot = 8.dp
    val dotBig = 12.dp
    val chip = 32.dp
    val nav = 64.dp            // the floating bottom menu
    val navItem = 48.dp
    val fab = 56.dp
    val leafTop = 28.dp        // the leaf in the header's top row
    val leafBig = 96.dp        // the leaf on the connection screen
    val pad = 72.dp            // a button on the lock screen's number pad
    val maxWidth = 600.dp
    val hairline = 1.dp
    val line = 2.dp
    val bar = 6.dp             // progress bar height
    val handleWidth = 32.dp
    val handleHeight = 4.dp
}

/** Corner radii: sheet 32, card 24, field 20, bubble 22 (6 on the "tail" corner), buttons and chips are round. */
object Radius {
    val sheet = RoundedCornerShape(32.dp, 32.dp, 0.dp, 0.dp)
    val card = RoundedCornerShape(24.dp)
    val tile = RoundedCornerShape(16.dp)
    val field = RoundedCornerShape(20.dp)
    val bubbleMine = RoundedCornerShape(22.dp, 22.dp, 6.dp, 22.dp)
    val bubbleTheirs = RoundedCornerShape(22.dp, 22.dp, 22.dp, 6.dp)
    val pill = CircleShape
}

/** Motion: time in milliseconds and fractions. All of the app's animations take their numbers only from here. */
object Motion {
    const val short = 150
    const val medium = 280
    const val rise = 440            // a card slides in from below
    const val stagger = 55          // delay between neighboring cards
    const val staggerMax = 8        // the delay stops growing past the eighth card
    const val fill = 900            // a bar and a number "fill in"
    const val leaf = 3000
    const val sweep = 1600
    const val shimmer = 1400        // a placeholder's running shimmer
    const val typing = 1000         // the "Sena is typing" dots
    const val skeletonDelay = 120L  // the placeholder doesn't appear right away: fast responses don't flash it
    const val tabSlide = 9          // tabs shift by 1/9 of their width
    const val pressScale = 0.97f
    const val enterScale = 0.96f
    const val springDamping = 0.62f
    const val springStiffness = 520f
}
