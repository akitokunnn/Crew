package local.crew.android.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.keyframes
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import local.crew.android.ComputerState
import local.crew.android.NearbyComputer
import local.crew.android.core.PairedComputer
import local.crew.android.ui.theme.LocalCrewColors

internal const val CODE_HELP = "去电脑 Crew「设置 → 局域网访问」生成配对码，扫码或输 8 位码"
internal const val SIGNED_OUT = "这台电脑已让你下线，请重新配对"

internal data class ConnectionIssue(val message: String, val network: Boolean = false, val invalidCode: Boolean = false)
internal data class PairTarget(val baseUrl: String, val hostName: String?, val instanceId: String? = null)

@Composable
internal fun PairingPage(
    computers: ComputerState,
    nearby: List<NearbyComputer>,
    searching: Boolean,
    discoveryError: String?,
    issue: ConnectionIssue?,
    manualInitially: Boolean,
    onScan: () -> Unit,
    onNearby: (NearbyComputer) -> Unit,
    onSelect: (PairedComputer) -> Unit,
    onForget: (PairedComputer) -> Unit,
    onSearch: () -> Unit,
    onManual: (String) -> Unit,
    onReturn: (() -> Unit)?,
) {
    var manual by rememberSaveable(manualInitially) { mutableStateOf(manualInitially) }
    var address by rememberSaveable { mutableStateOf("") }
    var forget by remember { mutableStateOf<PairedComputer?>(null) }
    val colors = LocalCrewColors.current
    PairingLayout(onReturn) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Headline("连接你的电脑")
            Body("在电脑打开 Crew，进「设置 → 局域网访问」打开开关")
        }
        if (issue != null) IssueCard(issue)
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            PrimaryAction("扫码连接", onScan)
            Meta("扫电脑上的二维码，一步连上")
        }
        if (computers.records.isNotEmpty()) {
            ComputerGroup("配对过的电脑") {
                computers.records.forEachIndexed { index, computer ->
                    if (index != 0) HorizontalDivider(color = colors.line)
                    val isNearby = nearby.any { it.instanceId == computer.instanceId }
                    val status = when {
                        computers.currentId == computer.instanceId && isNearby -> "上次使用 · 在线"
                        computers.currentId == computer.instanceId -> "上次使用"
                        isNearby -> "在线"
                        else -> "未在附近找到"
                    }
                    ComputerRow(computer.hostName, computer.baseUrl, status, { onSelect(computer) }) { forget = computer }
                }
            }
        }
        ComputerGroup("附近的电脑", if (searching) "正在寻找…" else null) {
            if (nearby.isEmpty()) {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Body(discoveryError ?: if (searching) "正在同一无线网里寻找电脑…" else "还没找到电脑")
                    if (!searching) {
                        Meta("确认手机和电脑连着同一个无线网，且电脑已开启局域网访问")
                        TextButton(onClick = onSearch) { Text("重新寻找") }
                    }
                }
            } else {
                nearby.forEachIndexed { index, found ->
                    if (index != 0) HorizontalDivider(color = colors.line)
                    val status = if (computers.records.any { it.instanceId == found.instanceId }) "已配对" else null
                    ComputerRow(found.hostName, found.baseUrl, status, { onNearby(found) })
                }
            }
        }
        Column {
            TextButton(onClick = { manual = !manual }, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(if (manual) "收起手动输入" else "找不到电脑？手动输入地址")
            }
            AnimatedVisibility(manual) {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value = address,
                        onValueChange = { address = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("电脑局域网地址") },
                        placeholder = { Text("例如 192.168.1.24:47812") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                        textStyle = TextStyle(fontSize = 15.sp, fontFamily = FontFamily.Monospace),
                    )
                    PrimaryAction("下一步", { onManual(address) }, enabled = address.isNotBlank())
                }
            }
        }
        Meta("只在信任的无线网下连接。电脑上可以随时移除这台手机。")
    }
    forget?.let { computer ->
        AlertDialog(
            onDismissRequest = { forget = null },
            title = { Text("忘记「${computer.hostName}」？") },
            text = { Body("下次连接这台电脑时，需要重新扫码或输入配对码。") },
            confirmButton = {
                TextButton(onClick = { forget = null; onForget(computer) }) {
                    Text("忘记这台电脑", color = colors.danger)
                }
            },
            dismissButton = { TextButton(onClick = { forget = null }) { Text("保留") } },
        )
    }
}

@Composable
internal fun CodePage(
    target: PairTarget,
    issue: ConnectionIssue?,
    busy: Boolean,
    onPair: (String) -> Unit,
    onScan: () -> Unit,
    onBack: () -> Unit,
) {
    var code by rememberSaveable(target) { mutableStateOf("") }
    var focused by remember { mutableStateOf(false) }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    val shake = remember { Animatable(0f) }
    var canCancel by remember { mutableStateOf(false) }
    LaunchedEffect(busy) {
        canCancel = false
        if (busy) {
            delay(4000)
            canCancel = true
        }
    }
    LaunchedEffect(issue, busy) {
        if (!busy) {
            if (issue?.invalidCode == true) {
                code = ""
                shake.animateTo(0f, keyframes {
                    durationMillis = 220
                    6f at 40
                    -6f at 90
                    4f at 140
                    0f at 220
                })
            }
            focus.requestFocus()
            keyboard?.show()
        }
    }
    PairingLayout(if (!busy || canCancel) onBack else null) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Headline("输入 8 位配对码")
            target.hostName?.let { Body(it) }
            Meta(target.baseUrl.removePrefix("http://"), monospace = true)
            Body(CODE_HELP)
        }
        val colors = LocalCrewColors.current
        BasicTextField(
            value = code,
            enabled = !busy,
            onValueChange = { value ->
                val digits = value.filter { it in '0'..'9' }.take(8)
                code = digits
                if (digits.length == 8) {
                    keyboard?.hide()
                    onPair(digits)
                }
            },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            singleLine = true,
            modifier = Modifier.fillMaxWidth().offset(x = shake.value.dp).focusRequester(focus)
                .onFocusChanged { focused = it.isFocused }
                .semantics {
                    contentDescription = "8 位数字配对码"
                    stateDescription = "已输入 ${code.length} 位，共 8 位"
                },
            decorationBox = { inner ->
                Box {
                    Box(Modifier.size(1.dp)) { inner() }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        for (group in 0..1) {
                            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                                for (index in group * 4 until group * 4 + 4) {
                                    val shape = RoundedCornerShape(12.dp)
                                    val border = if (focused && index == code.length) colors.accent else colors.lineStrong
                                    Box(
                                        Modifier.weight(1f).height(56.dp).background(colors.surface, shape)
                                            .border(1.dp, border, shape),
                                        contentAlignment = Alignment.Center,
                                    ) {
                                        Text(code.getOrNull(index)?.toString().orEmpty(),
                                            Modifier.clearAndSetSemantics {}, fontSize = 26.sp,
                                            fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Monospace)
                                    }
                                }
                            }
                        }
                    }
                }
            },
        )
        if (busy) {
            BusyText("正在连接 ${target.hostName ?: "电脑"}…")
            if (canCancel) TextButton(onClick = onBack) { Text("取消") }
        }
        if (!busy && issue != null) {
            IssueCard(issue)
            if (code.length == 8) PrimaryAction("再试一次", { onPair(code) })
        }
        if (!busy) TextButton(onClick = onScan) { Text("改用扫码连接") }
    }
}

@Composable
internal fun ReconnectPage(
    computer: PairedComputer,
    issue: ConnectionIssue,
    busy: Boolean,
    onRetry: () -> Unit,
    onSwitch: () -> Unit,
) {
    PairingLayout(onSwitch) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Headline("和电脑断开了连接")
            Body(computer.hostName)
            Meta(computer.baseUrl.removePrefix("http://"), monospace = true)
        }
        IssueCard(issue)
        if (busy) BusyText("正在重新连接 ${computer.hostName}…")
        PrimaryAction("重试", onRetry, !busy)
        TextButton(onClick = onSwitch) { Text("换一台") }
    }
}

@Composable
internal fun PairingLayout(onBack: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalCrewColors.current
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = 480.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(28.dp).background(colors.accent, RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
                    Text("C", Modifier.clearAndSetSemantics {}, color = colors.onAccent, fontWeight = FontWeight.Bold)
                }
                Spacer(Modifier.width(8.dp))
                Text("Crew", Modifier.weight(1f), fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                if (onBack != null) TextButton(onClick = onBack) { Text("返回") }
            }
            content()
        }
    }
}

@Composable
internal fun Headline(value: String) = Text(value, fontSize = 24.sp, lineHeight = 32.sp,
    letterSpacing = (-0.3).sp, fontWeight = FontWeight.SemiBold)

@Composable
internal fun Body(value: String) = Text(value, fontSize = 15.sp, lineHeight = 24.sp)

@Composable
internal fun Meta(value: String, monospace: Boolean = false) = Text(
    value, color = LocalCrewColors.current.text2, fontSize = 13.sp, lineHeight = 20.sp,
    fontFamily = if (monospace) FontFamily.Monospace else FontFamily.SansSerif,
)

@Composable
internal fun PrimaryAction(label: String, action: () -> Unit, enabled: Boolean = true) {
    Button(action, Modifier.fillMaxWidth().heightIn(min = 52.dp), enabled = enabled, shape = RoundedCornerShape(12.dp)) {
        Text(label, fontSize = 15.sp, lineHeight = 24.sp)
    }
}

@Composable
internal fun BusyText(label: String) {
    Row(Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        Body(label)
    }
}

@Composable
internal fun IssueCard(issue: ConnectionIssue) {
    val colors = LocalCrewColors.current
    val shape = RoundedCornerShape(12.dp)
    Column(Modifier.fillMaxWidth().background(colors.dangerSoft, shape).padding(14.dp)
        .semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(issue.message, color = colors.danger, fontSize = 15.sp, lineHeight = 24.sp, fontWeight = FontWeight.Medium)
        if (issue.network) {
            Meta("· 电脑上打开 Crew 的「局域网访问」")
            Meta("· 确认手机和电脑连着同一个无线网")
            Meta("· 检查电脑防火墙是否允许 Crew 连接")
        }
    }
}

@Composable
private fun ComputerGroup(title: String, status: String? = null, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalCrewColors.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(title, Modifier.semantics { heading() }, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            if (status != null) Meta("●  $status")
        }
        val shape = RoundedCornerShape(15.dp)
        Column(Modifier.fillMaxWidth().clip(shape).background(colors.surface).border(1.dp, colors.line, shape), content = content)
    }
}

@Composable
private fun ComputerRow(name: String, origin: String, status: String?, click: () -> Unit, forget: (() -> Unit)? = null) {
    val colors = LocalCrewColors.current
    Row(Modifier.fillMaxWidth().heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically) {
        Row(Modifier.weight(1f).clickable(role = Role.Button, onClick = click)
            .semantics(mergeDescendants = true) {
                if (forget != null) customActions = listOf(CustomAccessibilityAction("忘记这台电脑") { forget(); true })
            }.padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.size(23.dp)) {
                val line = 1.5.dp.toPx()
                drawRect(colors.accent, Offset(line, line), androidx.compose.ui.geometry.Size(size.width - line * 2, size.height * .63f),
                    style = androidx.compose.ui.graphics.drawscope.Stroke(line))
                drawLine(colors.accent, Offset(center.x, size.height * .68f), Offset(center.x, size.height * .88f), line)
                drawLine(colors.accent, Offset(size.width * .27f, size.height * .88f), Offset(size.width * .73f, size.height * .88f), line)
            }
            Column(Modifier.weight(1f)) {
                Text(name, fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium)
                Meta(origin.removePrefix("http://"), monospace = true)
            }
            if (status != null) {
                Text(status, Modifier.background(colors.accentSofter, RoundedCornerShape(50))
                    .padding(horizontal = 8.dp, vertical = 4.dp), color = colors.accentText, fontSize = 11.sp,
                    textAlign = TextAlign.Center)
            } else Text("›", color = colors.text2, fontSize = 20.sp)
        }
        if (forget != null) TextButton(onClick = forget, modifier = Modifier.heightIn(min = 48.dp)) { Text("忘记") }
    }
}
