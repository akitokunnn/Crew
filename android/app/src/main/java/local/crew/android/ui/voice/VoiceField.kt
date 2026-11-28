package local.crew.android.ui.voice

import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.*
import androidx.lifecycle.compose.LocalLifecycleOwner
import local.crew.android.ui.theme.LocalCrewColors
import local.crew.android.voice.CallState
import kotlin.math.*
import kotlinx.coroutines.*

private class RingPoints {
    val x = FloatArray(73) { cos(it*2*Math.PI/72).toFloat() }
    val y = FloatArray(73) { sin(it*2*Math.PI/72).toFloat() }
    val waves = Array(3) { harmonic ->
        val n = intArrayOf(2,3,5)[harmonic]
        Array(2) { axis -> FloatArray(73) { i ->
            if (axis == 0) sin(i*n*2*Math.PI/72).toFloat() else cos(i*n*2*Math.PI/72).toFloat()
        } }
    }
}

@Composable fun VoiceField(c: VoiceCallController,modifier: Modifier) {
    val colors = LocalCrewColors.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val resolver = LocalContext.current.contentResolver
    val points = remember { RingPoints() }
    val phase = remember { FloatArray(6) { if (it % 2 == 0) 1f else 0f } }
    var seconds by remember { mutableFloatStateOf(0f) }
    val static = c.preparing || c.muted || c.state == CallState.Offline ||
        Settings.Global.getFloat(resolver,Settings.Global.ANIMATOR_DURATION_SCALE,1f) == 0f
    val thinking = c.state == CallState.Thinking
    LaunchedEffect(lifecycle,static,thinking,c.reading) {
        if (!static) lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var previous = 0L
            var lastActive = 0L
            while (isActive) {
                withFrameNanos { now ->
                    if (previous == 0L) lastActive = now
                    if (c.level >= .05f || thinking || c.reading) lastActive = now
                    if (lastActive + 1_500_000_000L > now || now-previous >= 50_000_000L) {
                        seconds += if (previous == 0L) 0f else ((now-previous)*1e-9f).coerceAtMost(.1f)
                        for (i in 0..2) {
                            val a = seconds*(.55f+.13f*i)
                            phase[2*i] = cos(a); phase[2*i+1] = sin(a)
                        }
                        previous = now
                    }
                }
            }
        }
    }
    Canvas(modifier.drawWithCache {
        val path = Path()
        val r = size.minDimension*.39f
        val stroke = Stroke(width = 1.3.dp.toPx(),join = StrokeJoin.Round)
        val dash = stroke.copyEffect(PathEffect.dashPathEffect(floatArrayOf(.20f*r,.11f*r)))
        val center = Offset(size.width/2,size.height/2)
        onDrawBehind {
            val t = seconds
            val lv = if (static) 0f else (c.level*3).coerceIn(0f,1f)
            val spread = when { c.reading -> 1.08f; thinking -> .93f; else -> .84f }
            drawCircle(colors.accentSoft,r*.31f,center)
            for (i in 0 until 5) {
                path.rewind()
                for (k in 0..72) {
                    var wave = 0f
                    for (h in 0..2) {
                        val index = (k + i * (h+3)) % 72
                        wave += (points.waves[h][0][index]*phase[h*2]+points.waves[h][1][index]*phase[h*2+1])/(h+2)
                    }
                    val radius = r*((.3f+.175f*i)*spread*(1f+.05f*(i+1)*lv)+.04f*(.38f+lv)*wave)
                    val x = center.x+radius*points.x[k]
                    val y = center.y+radius*points.y[k]
                    if (k == 0) path.moveTo(x,y) else path.lineTo(x,y)
                }
                path.close()
                rotate(t*(if (thinking) 31.5f else 2.86f)*(if (i%2==0) 1 else -1),center) {
                    drawPath(path,colors.accent,alpha = (if (static) .62f else .84f)-.045f*i,style = if (thinking) dash else stroke)
                }
            }
            if (!static && !thinking) for (j in 0..2) {
                val p = (t*.18f+j/3f)%1f
                val radius = if (c.reading) p else 1f-p
                drawCircle(colors.accent,r*(.22f+radius*.85f),center,alpha = .12f*(1-p),style = stroke)
            }
        }
    }) {}
}
private fun Stroke.copyEffect(effect: PathEffect) = Stroke(width = width,join = join,pathEffect = effect)
