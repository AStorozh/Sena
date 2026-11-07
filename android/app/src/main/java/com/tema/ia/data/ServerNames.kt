package com.tema.ia.data

// Names and strings understood by the Sena server (it works in Russian). These are not screen text.

/** Notes folder on the server where new entries from the app land. */
const val INBOX_FOLDER = "Входящие"

/** Tag for a rating given from the app. */
const val OUTCOME_REASON = "оценка из приложения"

/** Default facts section. */
const val DEFAULT_FACT_SECTION = "Обо мне"

/** The kinds of what search finds (the memory index's kinds, sena/memory/index.py KINDS): the keys the server sends, not screen text. */
object SearchKind {
    const val TASK = "задача"
    const val FACT = "факт"
    const val PERSON = "человек"
    const val NOTE = "заметка"
    const val RULE = "правило"
    const val SKILL = "навык"
    const val LESSON = "урок"
    const val GOAL = "цель"
    const val MESSAGE = "сообщение"
    const val HEARD = "услышано"
    const val FILE = "файл"
    const val PROJECT = "проект"
    const val ORGANIZATION = "организация"
    const val PLACE = "место"
}
