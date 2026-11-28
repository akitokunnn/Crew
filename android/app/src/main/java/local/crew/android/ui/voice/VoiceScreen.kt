package local.crew.android.ui.voice

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.*
import kotlinx.coroutines.delay
import local.crew.android.R
import local.crew.android.ui.theme.LocalCrewColors
import local.crew.android.voice.CallState
import local.crew.android.voice.Confirmation

@Composable
fun VoiceScreen(c: VoiceCallController, finish: () -> Unit, retry: () -> Unit) {
    val colors = LocalCrewColors.current
    var showDiagnostics by remember { mutableStateOf(false) }
    var seconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(c.state) {
        seconds = 0
        if (c.state == CallState.Thinking) while (true) { delay(1000); seconds++ }
    }
    val status = when {
        c.preparing -> "准备中"
        c.state == CallState.Offline -> "已断开"
        c.muted -> "已静音"
        c.partial.isNotEmpty() -> "在听"
        c.reading -> "在说"
        else -> when (c.state) {
            CallState.Idle, CallState.Connecting -> "连接中"
            CallState.Listening -> "在听"
            CallState.Thinking -> "在想"
            CallState.Speaking -> "在说"
            CallState.Muted -> "已静音"
            CallState.Offline -> "已断开"
            CallState.AwaitingConfirm -> "待确认"
        }
    }
    val caption = when {
        c.partial.isNotBlank() -> c.partial
        c.reading -> c.line
        c.state == CallState.Thinking -> when {
            seconds < 5 -> "好，我看一下…"
            seconds < 15 -> "正在查看工作区情况 · ${seconds}秒"
            else -> "还在整理结果 · ${seconds}秒"
        }
        c.line.isNotEmpty() -> c.line
        c.preparing -> "正在准备语音（几秒）"
        c.state == CallState.Offline -> "连接中断，正在重连"
        c.state == CallState.AwaitingConfirm -> "请在屏幕上点确认"
        c.state == CallState.Listening -> if (c.timeline.isEmpty()) "我在。想听今天的汇报，还是派个活？" else "你说，我在听…"
        c.state == CallState.Idle || c.state == CallState.Connecting -> "正在连接电脑"
        else -> status
    }
    val listState = rememberLazyListState()
    val latest = c.timeline.lastIndex
    LaunchedEffect(latest) {
        if (latest >= 0) listState.animateScrollToItem(latest)
    }

    BoxWithConstraints(Modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 16.dp)) {
        val stageHeight = minOf(if (c.cards.isEmpty()) 204.dp else 140.dp, maxHeight * 0.25f)
        val cardHeight = maxHeight * 0.45f
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
            VoiceTopBar(c, status, finish) { showDiagnostics = true }
            Column(Modifier.widthIn(max = 680.dp).fillMaxWidth().weight(1f)) {
                if (c.preparing) LinearProgressIndicator(Modifier.fillMaxWidth())
                VoiceField(c, Modifier.fillMaxWidth().height(stageHeight))
                VoiceCaption(caption, if (caption == c.line) c.progress else 1f)
                if (c.error.isNotEmpty()) {
                    Text(c.error, color = colors.danger, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    if (c.preparationFailed) OutlinedButton(onClick = retry) { Text("重试") }
                }
                LazyColumn(
                    Modifier.fillMaxWidth().weight(1f), state = listState,
                    verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.Bottom),
                    contentPadding = PaddingValues(vertical = 12.dp),
                ) {
                    items(c.timeline, key = { it.key }) { entry -> TranscriptBubble(entry) }
                }
                if (c.generation || c.reading || c.preparingSentence) {
                    Row(Modifier.align(Alignment.CenterHorizontally), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = c::stopReading) { Text("停止朗读") }
                        TextButton(onClick = c::cancelGeneration, enabled = c.generation) { Text("停下助手") }
                    }
                }
            }
            if (c.cards.isNotEmpty()) {
                Column(
                    Modifier.widthIn(max = 680.dp).fillMaxWidth().heightIn(max = cardHeight).verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    for (card in c.cards) key(card.id) {
                        ConfirmationCard(card, c.flashCount) { c.decide(card.id, it) }
                    }
                }
            }
            Surface(
                Modifier.widthIn(max = 520.dp).fillMaxWidth().padding(top = 8.dp, bottom = 12.dp),
                shape = RoundedCornerShape(32.dp), color = colors.studioGlass, border = BorderStroke(1.dp, colors.studioBorder),
            ) {
                Row(Modifier.padding(6.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
                    val muteLabel = if (c.muted) "已静音" else "静音"
                    Control(R.drawable.voice_mic, muteLabel, muteLabel, c::toggleMute, muted = c.muted)
                    Control(R.drawable.voice_speaker, if (c.speaker) "外放" else "听筒",
                        if (c.speaker) "外放中，切换到听筒" else "听筒中，切换到外放", c::toggleRoute)
                    Control(R.drawable.voice_text, "文字", "返回文字对话", finish)
                    Control(R.drawable.voice_hangup, "挂断", "挂断语音通话", finish, danger = true)
                }
            }
        }
    }
    if (showDiagnostics) AlertDialog(
        onDismissRequest = { showDiagnostics = false },
        title = { Text("排查问题") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (c.systemVoice) "朗读 · 系统音色" else "朗读 · 内置音色")
                for (row in c.diagnostics) Text(row)
                if (c.error.isNotBlank()) Text(c.error)
            }
        },
        confirmButton = { TextButton(onClick = { showDiagnostics = false }) { Text("关闭") } },
    )
}

@Composable
private fun VoiceTopBar(c: VoiceCallController, status: String, finish: () -> Unit, diagnostics: () -> Unit) {
    val colors = LocalCrewColors.current
    var expanded by remember { mutableStateOf(false) }
    Box(Modifier.widthIn(max = 680.dp).fillMaxWidth().height(56.dp)) {
        TextButton(
            onClick = finish, modifier = Modifier.align(Alignment.CenterStart).heightIn(min = 48.dp),
            contentPadding = PaddingValues(horizontal = 4.dp),
        ) { Text("‹ 文字", maxLines = 1) }
        Column(Modifier.align(Alignment.Center).padding(horizontal = 118.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(c.project, color = colors.text, fontSize = 16.sp, fontWeight = FontWeight.Medium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("工作区助手", color = colors.text3, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Row(Modifier.align(Alignment.CenterEnd), verticalAlignment = Alignment.CenterVertically) {
            Row(
                Modifier.widthIn(max = 70.dp).height(28.dp).background(colors.accentSoft, CircleShape)
                    .padding(horizontal = 10.dp).semantics { liveRegion = LiveRegionMode.Polite },
                horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically,
            ) {
                val dot = if (c.state == CallState.Offline) colors.danger else colors.accent
                Box(Modifier.size(7.dp).background(dot, CircleShape))
                Text(status, color = colors.accentText, fontSize = 12.sp, fontWeight = FontWeight.Medium,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Box {
                IconButton(
                    onClick = { expanded = true },
                    modifier = Modifier.size(48.dp).semantics { contentDescription = "更多选项" },
                ) { Text("⋯", Modifier.clearAndSetSemantics {}, color = colors.text, fontSize = 24.sp) }
                DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                    DropdownMenuItem(text = { Text(if (c.systemVoice) "声音：系统" else "声音：内置") },
                        enabled = c.canSwitch, onClick = { expanded = false; c.toggleVoice() })
                    DropdownMenuItem(text = { Text("排查问题") }, onClick = { expanded = false; diagnostics() })
                }
            }
        }
    }
}

@Composable
private fun VoiceCaption(caption: String, progress: Float) {
    val colors = LocalCrewColors.current
    val count = caption.codePointCount(0, caption.length)
    val split = caption.offsetByCodePoints(0, (count * progress).toInt().coerceIn(0, count))
    val long = count > 30
    Text(
        buildAnnotatedString {
            withStyle(SpanStyle(color = colors.text)) { append(caption.substring(0, split)) }
            withStyle(SpanStyle(color = colors.text2)) { append(caption.substring(split)) }
        },
        Modifier.fillMaxWidth().padding(vertical = 10.dp),
        fontSize = if (long) 18.sp else 20.sp, lineHeight = if (long) 29.sp else 32.sp,
        letterSpacing = 0.2.sp, maxLines = 3, overflow = TextOverflow.Ellipsis,
    )
}

@Composable
private fun TranscriptBubble(entry: TranscriptLine) {
    val colors = LocalCrewColors.current
    if (entry.user) {
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            val shape = RoundedCornerShape(16.dp, 16.dp, 4.dp, 16.dp)
            Text(entry.text, Modifier.widthIn(max = maxWidth * 0.82f).background(colors.userBubble, shape).padding(12.dp),
                color = colors.text, fontSize = 14.sp, lineHeight = 23.sp)
        }
    } else {
        Column(Modifier.fillMaxWidth()) {
            if (entry.interrupted) Text("助手 · 已打断", style = MaterialTheme.typography.labelSmall, color = colors.text2)
            Text(entry.text, color = colors.text, fontSize = 14.sp, lineHeight = 23.sp)
        }
    }
}

@Composable
private fun ConfirmationCard(card: Confirmation, flashCount: Int, decide: (Boolean) -> Unit) {
    val colors = LocalCrewColors.current
    var lit by remember { mutableStateOf(false) }
    LaunchedEffect(flashCount) { if (flashCount > 0) { lit = true; delay(420); lit = false } }
    val border by animateColorAsState(if (lit) colors.accentStrong else colors.warnLine, label = "确认提醒")
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(15.dp),
        color = if (lit) colors.accentSoft else colors.studioCard, border = BorderStroke(if (lit) 2.dp else 1.dp, border)) {
        Column(Modifier.padding(14.dp)) {
            Text("需要你确认", color = colors.accentText, style = MaterialTheme.typography.labelLarge)
            Text(card.summary, Modifier.padding(vertical = 8.dp).semantics { liveRegion = LiveRegionMode.Assertive }, color = colors.text)
            val shape = RoundedCornerShape(12.dp)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { decide(true) }, modifier = Modifier.weight(1f).heightIn(min = 48.dp), shape = shape) {
                    Text("确认执行", fontSize = 15.sp, fontWeight = FontWeight.Medium)
                }
                OutlinedButton(onClick = { decide(false) }, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    shape = shape, border = BorderStroke(1.dp, colors.lineStrong)) {
                    Text("取消", fontSize = 15.sp, fontWeight = FontWeight.Medium)
                }
            }
            Text("为防误触，语音不能直接确认，请点上方按钮", Modifier.padding(top = 8.dp), color = colors.text2, fontSize = 12.sp)
        }
    }
}

@Composable
private fun RowScope.Control(icon: Int, label: String, description: String, action: () -> Unit,
    danger: Boolean = false, muted: Boolean? = null) {
    val colors = LocalCrewColors.current
    val tint = when {
        danger -> MaterialTheme.colorScheme.onError
        muted == true -> colors.accentText
        else -> colors.text
    }
    Box(
        Modifier.weight(1f).heightIn(min = 52.dp).clip(RoundedCornerShape(26.dp))
            .background(when { danger -> colors.danger; muted == true -> colors.accentSoft; else -> Color.Transparent })
            .clickable(role = if (muted == null) Role.Button else Role.Switch, onClick = action)
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center,
    ) {
        Column(Modifier.padding(vertical = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(painterResource(icon), null, Modifier.size(22.dp), tint = tint)
            Text(label, Modifier.clearAndSetSemantics {}, color = tint, style = MaterialTheme.typography.labelMedium)
        }
    }
}
