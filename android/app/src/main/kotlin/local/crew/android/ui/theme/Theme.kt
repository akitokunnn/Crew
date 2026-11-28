package local.crew.android.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf

val LocalCrewColors = staticCompositionLocalOf<CrewColors> {
    error("CrewTheme is required")
}

private val lightScheme = with(LightCrewColors) {
    lightColorScheme(
        primary = accent,
        onPrimary = onAccent,
        background = bg,
        onBackground = text,
        surface = surface,
        onSurface = text,
        onSurfaceVariant = text2,
        surfaceContainer = studioCard,
        outline = lineStrong,
        error = danger,
        onError = surface2,
    )
}

private val darkScheme = with(DarkCrewColors) {
    darkColorScheme(
        primary = accent,
        onPrimary = onAccent,
        background = bg,
        onBackground = text,
        surface = surface,
        onSurface = text,
        onSurfaceVariant = text2,
        surfaceContainer = studioCard,
        outline = lineStrong,
        error = danger,
        onError = panel,
    )
}

@Composable
fun CrewTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    CompositionLocalProvider(LocalCrewColors provides if (dark) DarkCrewColors else LightCrewColors) {
        MaterialTheme(colorScheme = if (dark) darkScheme else lightScheme, content = content)
    }
}
