package com.tema.ia.ui

import android.content.res.Resources
import com.tema.ia.R
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

// Time and money in words: "just now", "12 min ago", "yesterday at 18:40", "$0.03".

object Fmt {
    /** Dollars to the cent; a sum under a cent keeps its first digits ("$0,0032"), as the Mac's cost report writes it, not "$0,00". */
    fun money(value: Double, locale: Locale): String = "$" + when {
        value > 0 && value < 0.0001 -> String.format(locale, "%.6f", value)
        value > 0 && value < 0.01 -> String.format(locale, "%.4f", value)
        else -> String.format(locale, "%.2f", value)
    }

    /** A number as people write it: whole without a fraction ("90000"), otherwise as it is ("0.5"). */
    fun number(value: Double): String = if (value == Math.rint(value) && !value.isInfinite()) value.toLong().toString() else value.toString()

    fun hhmm(epochSeconds: Double, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String =
        DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale).format(Instant.ofEpochSecond(epochSeconds.toLong()).atZone(zone))

    /** "just now", "12 min ago", "3 h ago", "yesterday at 18:40", then a date. */
    fun ago(res: Resources, epochSeconds: Double, nowSeconds: Double, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String {
        val diff = (nowSeconds - epochSeconds).toLong()
        if (diff < 60) return res.getString(R.string.time_now)
        if (diff < 3600) return res.getString(R.string.time_min_ago, (diff / 60).toInt())
        val moment = Instant.ofEpochSecond(epochSeconds.toLong()).atZone(zone)
        val today = Instant.ofEpochSecond(nowSeconds.toLong()).atZone(zone).toLocalDate()
        val hhmm = hhmm(epochSeconds, zone, locale)
        return when {
            moment.toLocalDate() == today && diff < 6 * 3600 -> res.getString(R.string.time_hours_ago, (diff / 3600).toInt())
            moment.toLocalDate() == today -> res.getString(R.string.time_today, hhmm)
            moment.toLocalDate() == today.minusDays(1) -> res.getString(R.string.time_yesterday, hhmm)
            else -> DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale).format(moment) + ", " + hhmm
        }
    }

    fun duration(res: Resources, seconds: Double): String {
        val s = seconds.toLong().coerceAtLeast(0)
        return when {
            s < 60 -> res.getString(R.string.duration_sec, s.toInt())
            s < 3600 -> res.getString(R.string.duration_min, (s / 60).toInt())
            else -> res.getString(R.string.duration_hours, (s / 3600).toInt(), ((s % 3600) / 60).toInt())
        }
    }

    /** The calendar day of a moment, in the device's zone: chat headings "Today", "Yesterday", otherwise a date. */
    fun day(epochSeconds: Double, zone: ZoneId = ZoneId.systemDefault()): LocalDate = Instant.ofEpochSecond(epochSeconds.toLong()).atZone(zone).toLocalDate()
}
