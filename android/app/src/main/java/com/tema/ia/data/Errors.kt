package com.tema.ia.data

enum class ApiError {
    NoServer,       // the Mac isn't responding at this address
    BadToken,       // the password didn't match
    Locked,         // the address is locked out for password guessing
    BadAnswer,      // the server's answer made no sense
    Server,         // the server returned an error with an explanation
}

/**
 * [code] — the HTTP status when the server answered (0 when it didn't): a 404 "no such action" or a 400 tells an older server
 * from a newer one. [timedOut] — the request left but no answer came in time: the server may still be doing it.
 */
class ApiException(val kind: ApiError, override val message: String, val code: Int = 0, val timedOut: Boolean = false) : Exception(message)
