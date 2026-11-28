package local.crew.android.ui.voice

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.*
import local.crew.android.ui.theme.LocalCrewColors
import local.crew.android.R
import local.crew.android.voice.CallState
import kotlinx.coroutines.delay

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun VoiceScreen(c: VoiceCallController, finish: () -> Unit, retry: () -> Unit) {
    val colors = LocalCrewColors.current
    var showDiagnostics by remember { mutableStateOf(false) }
    var seconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(c.state) {
        seconds = 0
        if (c.state == CallState.Thinking) while (true) { delay(1000); seconds++ }
    }
    val label = when {
        c.preparing -> "正在准备语音（几秒）"
        c.partial.isNotEmpty() -> "正在听你说"
        c.reading -> "助手在说"
        else -> when (c.state) {
            CallState.Idle, CallState.Connecting -> "正在连接电脑"
            CallState.Listening -> "在听"
            CallState.Thinking -> "助手在想"
            CallState.Speaking -> "助手在说"
            CallState.Muted -> "已闭麦"
            CallState.Offline -> "连接中断，正在重连"
            CallState.AwaitingConfirm -> "等你点确认"
        }
    }
    Column(Modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 16.dp),horizontalAlignment = Alignment.CenterHorizontally) {
        LazyColumn(Modifier.weight(1f).widthIn(max = 680.dp).fillMaxWidth(),verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(vertical = 12.dp)) {
            item {
                Text(c.project,style = MaterialTheme.typography.headlineMedium,fontWeight = FontWeight.SemiBold,color = colors.text)
                FlowRow(verticalArrangement = Arrangement.Center,horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically,modifier = Modifier.heightIn(min = 48.dp)) {
                        Box(Modifier.size(8.dp).background(if (c.state == CallState.Offline) colors.warn else colors.accent,RoundedCornerShape(50)))
                        Spacer(Modifier.width(7.dp))
                        Text(label,color = colors.text2,modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    }
                    if (c.canSwitch) TextButton(onClick = c::toggleVoice) { Text(if (c.systemVoice) "系统音色 ⇄" else "内置音色 ⇄") }
                    TextButton(onClick = { showDiagnostics = true }) { Text("诊断") }
                }
                if (c.preparing) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
            item {
                VoiceField(c,Modifier.fillMaxWidth().height(if (c.cards.isEmpty()) 204.dp else 140.dp))
                Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp,vertical = 10.dp)) {
                    val text = when {
                        c.partial.isNotBlank() -> c.partial
                        c.reading -> c.line
                        c.state == CallState.Thinking -> when {
                            seconds < 5 -> "好，我看一下…"
                            seconds < 15 -> "正在查看工作区情况 · " + seconds + "秒"
                            else -> "还在整理结果 · " + seconds + "秒"
                        }
                        c.line.isNotEmpty() -> c.line
                        c.state == CallState.AwaitingConfirm -> "请在屏幕上点确认"
                        c.state == CallState.Listening -> if (c.timeline.isEmpty()) "我在。想听今天的汇报，还是派个活？" else "你说，我在听…"
                        else -> label
                    }
                    val split = if (text == c.line) text.offsetByCodePoints(0,
                        (text.codePointCount(0,text.length)*c.progress).toInt().coerceIn(0,text.codePointCount(0,text.length))) else text.length
                    Text(buildAnnotatedString {
                        withStyle(SpanStyle(color = colors.text)) { append(text.substring(0,split)) }
                        withStyle(SpanStyle(color = colors.text2)) { append(text.substring(split)) }
                    },style = MaterialTheme.typography.headlineSmall)
                }
            }
            // Pending decisions stay above the transcript, reachable even in long conversations.
            items(c.cards,key = { "card-" + it.id }) { card ->
                var lit by remember { mutableStateOf(false) }
                LaunchedEffect(c.flashCount) { if (c.flashCount > 0) { lit = true; delay(420); lit = false } }
                val border by animateColorAsState(if (lit) colors.accentStrong else colors.warnLine,label = "确认提醒")
                Surface(modifier = Modifier.fillMaxWidth(),shape = RoundedCornerShape(16.dp),color = if (lit) colors.accentSoft else colors.studioCard,
                    border = BorderStroke(if (lit) 2.dp else 1.dp,border)) {
                    Column(Modifier.padding(14.dp)) {
                        Text("等你确认",color = colors.accentText,style = MaterialTheme.typography.labelLarge)
                        Text(card.summary,Modifier.padding(vertical = 8.dp).semantics { liveRegion = LiveRegionMode.Assertive },color = colors.text)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { c.decide(card.id,true) }) { Text("确认") }
                            OutlinedButton(onClick = { c.decide(card.id,false) }) { Text("取消") }
                        }
                    }
                }
            }
            if (c.error.isNotEmpty()) item {
                Text(c.error,color = colors.danger,modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                if (c.preparationFailed) OutlinedButton(onClick = retry) { Text("重试") }
            }
            items(c.timeline.reversed(),key = { "line-" + it.key }) { entry ->
                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
                    .background(if (entry.user) colors.userBubble else colors.studioCard).padding(12.dp)) {
                    Text(if (entry.user) "你" else if (entry.interrupted) "助手 · 已打断" else "助手",style = MaterialTheme.typography.labelSmall,color = colors.text2)
                    Text(entry.text,color = colors.text)
                }
            }
        }
        if (c.generation || c.reading || c.preparingSentence) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = c::stopReading) { Text("停止朗读") }
                TextButton(onClick = c::cancelGeneration,enabled = c.generation) { Text("取消生成") }
            }
        }
        Surface(Modifier.widthIn(max = 520.dp).fillMaxWidth().padding(vertical = 8.dp),
            shape = RoundedCornerShape(28.dp),color = colors.studioGlass,border = BorderStroke(1.dp,colors.studioBorder)) {
            Row(Modifier.padding(4.dp),horizontalArrangement = Arrangement.SpaceEvenly) {
                Control(R.drawable.voice_mic,if (c.muted) "开麦" else "闭麦","麦克风静音",c::toggleMute,muted = c.muted)
                Control(R.drawable.voice_speaker,if (c.speaker) "外放" else "听筒",if (c.speaker) "外放中，切换到听筒" else "听筒中，切换到外放",c::toggleRoute)
                Control(R.drawable.voice_text,"文字","返回文字对话",finish)
                Control(R.drawable.voice_hangup,"挂断","挂断语音通话",finish,danger = true)
            }
        }
    }
    if (showDiagnostics) AlertDialog(onDismissRequest = { showDiagnostics = false },title = { Text("语音诊断") },
        text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(if (c.systemVoice) "朗读 · 系统音色" else "朗读 · 内置音色")
            for (row in c.diagnostics) Text(row)
            if (c.error.isNotBlank()) Text(c.error)
        } },confirmButton = { TextButton(onClick = { showDiagnostics = false }) { Text("关闭") } })
}

@Composable private fun RowScope.Control(icon: Int,label: String, description: String, action: () -> Unit, danger: Boolean = false, muted: Boolean? = null) {
    val colors = LocalCrewColors.current
    Box(Modifier.weight(1f).heightIn(min = 48.dp).clip(RoundedCornerShape(24.dp))
        .background(when { danger -> colors.dangerSoft; muted == true -> colors.accentSoft; else -> androidx.compose.ui.graphics.Color.Transparent })
        .clickable(role = if (muted == null) Role.Button else Role.Switch,onClick = action).semantics {
            contentDescription = description
            if (muted != null) stateDescription = if (muted) "已静音" else "收音中"
        },contentAlignment = Alignment.Center) {
        val tint = if (danger) colors.danger else colors.text
        Column(Modifier.padding(vertical = 6.dp),horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(painterResource(icon),null,Modifier.size(20.dp),tint = tint)
            Text(label,color = tint,style = MaterialTheme.typography.labelMedium)
        }
    }
}
