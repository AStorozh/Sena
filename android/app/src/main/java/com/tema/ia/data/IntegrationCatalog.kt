package com.tema.ia.data

import androidx.annotation.StringRes
import com.tema.ia.R

/**
 * The app's list of integrations. It must match the server's list (desk.INTEGRATIONS);
 * this is checked by tests/test_android.py (test_integration_list_matches_the_server) in the Sena project.
 */
object IntegrationCatalog {
    enum class Group { Comm, Work, Data, Sena }

    class Spec(
        val id: String,
        val group: Group,
        @StringRes val name: Int,
        @StringRes val role: Int,
        @StringRes val how: Int,
        val keys: List<String>,
        val anyKey: Boolean = false,     // any single key is enough (maps)
        val optional: Boolean = false,   // for a server that doesn't say "needed": without its key it is simply not used, not a problem
    )

    val all: List<Spec> = listOf(
    Spec("telegram_bot", Group.Comm, R.string.int_telegram_bot_name, R.string.int_telegram_bot_role, R.string.int_telegram_bot_how, listOf("TELEGRAM_TOKEN", "TG_OWNER_ID")),
    Spec("telegram_account", Group.Comm, R.string.int_telegram_account_name, R.string.int_telegram_account_role, R.string.int_telegram_account_how, listOf("TG_API_ID", "TG_API_HASH")),
    Spec("app", Group.Comm, R.string.int_app_name, R.string.int_app_role, R.string.int_app_how, listOf("APP_TOKEN")),
    Spec("phone", Group.Comm, R.string.int_phone_name, R.string.int_phone_role, R.string.int_phone_how, listOf("APP_TOKEN")),
    Spec("hearing", Group.Comm, R.string.int_hearing_name, R.string.int_hearing_role, R.string.int_hearing_how, listOf()),
    Spec("browser", Group.Work, R.string.int_browser_name, R.string.int_browser_role, R.string.int_browser_how, listOf()),
    Spec("mac", Group.Work, R.string.int_mac_name, R.string.int_mac_role, R.string.int_mac_how, listOf()),
    Spec("claude", Group.Work, R.string.int_claude_name, R.string.int_claude_role, R.string.int_claude_how, listOf()),
    Spec("google", Group.Work, R.string.int_google_name, R.string.int_google_role, R.string.int_google_how, listOf()),
    Spec("github", Group.Work, R.string.int_github_name, R.string.int_github_role, R.string.int_github_how, listOf("SENA_GITHUB_TOKEN")),
    Spec("hireflow", Group.Work, R.string.int_hireflow_name, R.string.int_hireflow_role, R.string.int_hireflow_how, listOf("HIREFLOW_KEY")),
    Spec("site", Group.Work, R.string.int_site_name, R.string.int_site_role, R.string.int_site_how, listOf("SENA_SITE_KEY")),
    Spec("youtube", Group.Work, R.string.int_youtube_name, R.string.int_youtube_role, R.string.int_youtube_how, listOf()),
    Spec("luma", Group.Work, R.string.int_luma_name, R.string.int_luma_role, R.string.int_luma_how, listOf()),
    Spec("mcp", Group.Work, R.string.int_mcp_name, R.string.int_mcp_role, R.string.int_mcp_how, listOf()),
    Spec("sources", Group.Data, R.string.int_sources_name, R.string.int_sources_role, R.string.int_sources_how, listOf()),
    Spec("maps", Group.Data, R.string.int_maps_name, R.string.int_maps_role, R.string.int_maps_how, listOf("TOMTOM_KEY", "GOOGLE_MAPS_KEY"), anyKey = true),
    Spec("travel", Group.Data, R.string.int_travel_name, R.string.int_travel_role, R.string.int_travel_how, listOf("TRAVELPAYOUTS_TOKEN")),
    Spec("gemini", Group.Data, R.string.int_gemini_name, R.string.int_gemini_role, R.string.int_gemini_how, listOf("GEMINI_API_KEY")),
    Spec("scheduler", Group.Sena, R.string.int_scheduler_name, R.string.int_scheduler_role, R.string.int_scheduler_how, listOf()),
    Spec("memory", Group.Sena, R.string.int_memory_name, R.string.int_memory_role, R.string.int_memory_how, listOf()),
    Spec("home", Group.Sena, R.string.int_home_name, R.string.int_home_role, R.string.int_home_how, listOf()),
    Spec("obsidian", Group.Sena, R.string.int_obsidian_name, R.string.int_obsidian_role, R.string.int_obsidian_how, listOf()),
    Spec("claude_api", Group.Sena, R.string.int_claude_api_name, R.string.int_claude_api_role, R.string.int_claude_api_how, listOf("SENA_CLAUDE_BRAIN_KEY")),
    Spec("openai", Group.Sena, R.string.int_openai_name, R.string.int_openai_role, R.string.int_openai_how, listOf("OPENAI_API_KEY"), optional = true),
    )

    fun by(id: String): Spec? = all.firstOrNull { it.id == id }
}
