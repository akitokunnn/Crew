package local.crew.android.ui

import android.view.ViewGroup
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.zxing.BinaryBitmap
import com.google.zxing.ChecksumException
import com.google.zxing.FormatException
import com.google.zxing.NotFoundException
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import local.crew.android.ui.theme.LocalCrewColors

@Composable
internal fun ScannerPage(
    granted: Boolean,
    busy: Boolean,
    issue: ConnectionIssue?,
    onScanned: (String) -> Unit,
    onSettings: () -> Unit,
    onManual: () -> Unit,
    onBack: () -> Unit,
    onScanAgain: () -> Unit,
) {
    var camera by remember { mutableStateOf<Camera?>(null) }
    var torch by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    PairingLayout(onBack) {
        Headline("扫电脑上的二维码")
        Box(Modifier.fillMaxWidth().height(290.dp).clip(RoundedCornerShape(15.dp)).background(Color(0xFF191917)),
            contentAlignment = Alignment.Center) {
            if (granted && issue == null && cameraError == null) {
                CameraPreview(
                    Modifier.matchParentSize(), onScanned,
                    onCamera = { camera = it },
                    onError = { cameraError = it },
                )
            }
            if (granted && issue == null && cameraError == null) {
                val accent = LocalCrewColors.current.accentStrong
                Canvas(Modifier.size(232.dp)) {
                    val arm = 22.dp.toPx()
                    val stroke = 3.dp.toPx()
                    for (x in listOf(0f, size.width)) for (y in listOf(0f, size.height)) {
                        val dx = if (x == 0f) arm else -arm
                        val dy = if (y == 0f) arm else -arm
                        drawLine(accent, Offset(x, y), Offset(x + dx, y), stroke)
                        drawLine(accent, Offset(x, y), Offset(x, y + dy), stroke)
                    }
                }
            }
            if (!granted) Text("需要相机权限来扫码", color = Color.White)
        }
        Body("在电脑 Crew「设置 → 局域网访问」里生成二维码")
        if (busy) BusyText("正在连接电脑…")
        if (issue != null) {
            IssueCard(issue)
            TextButton(onClick = onScanAgain) { Text("重新扫码") }
        }
        cameraError?.let { IssueCard(ConnectionIssue(it)) }
        if (!granted) {
            Meta("只在扫码连接电脑时使用相机。你也可以手动输入配对码。")
            PrimaryAction("去开启相机权限", onSettings)
        } else if (issue == null && camera?.cameraInfo?.hasFlashUnit() == true) {
            TextButton(onClick = {
                val next = !torch
                camera?.cameraControl?.enableTorch(next)
                torch = next
            }) { Text(if (torch) "关灯" else "照亮") }
        }
        TextButton(onClick = onManual) { Text("改输配对码") }
    }
}

@Composable
private fun CameraPreview(
    modifier: Modifier,
    onScanned: (String) -> Unit,
    onCamera: (Camera) -> Unit,
    onError: (String) -> Unit,
) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val view = remember(context) {
        PreviewView(context).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }
    val scanned by rememberUpdatedState(onScanned)
    val cameraReady by rememberUpdatedState(onCamera)
    val failed by rememberUpdatedState(onError)
    AndroidView(factory = { view }, modifier = modifier)
    DisposableEffect(owner, view) {
        val worker = Executors.newSingleThreadExecutor()
        val main = ContextCompat.getMainExecutor(context)
        val provider = ProcessCameraProvider.getInstance(context)
        val stopped = AtomicBoolean(false)
        val delivered = AtomicBoolean(false)
        var bound: ProcessCameraProvider? = null
        val preview = Preview.Builder().build().also { it.surfaceProvider = view.surfaceProvider }
        val analysis = ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
        val reader = QRCodeReader()
        analysis.setAnalyzer(worker) { image ->
            image.use {
                if (!delivered.get()) {
                    val text = decodeQr(reader, image)
                    if (text != null && delivered.compareAndSet(false, true)) {
                        main.execute { if (!stopped.get()) scanned(text) }
                    }
                }
            }
        }
        provider.addListener({
            if (!stopped.get()) {
                try {
                    val cameras = provider.get()
                    bound = cameras
                    val camera = cameras.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                    cameraReady(camera)
                } catch (failure: ExecutionException) {
                    failed("相机启动失败（${failure.cause?.javaClass?.simpleName ?: failure.javaClass.simpleName}）")
                } catch (failure: IllegalArgumentException) {
                    failed("这台手机没有可用的后置相机（${failure.javaClass.simpleName}）")
                } catch (failure: IllegalStateException) {
                    failed("相机暂时无法使用（${failure.javaClass.simpleName}）")
                } catch (failure: SecurityException) {
                    failed("相机权限已关闭（${failure.javaClass.simpleName}）")
                }
            }
        }, main)
        onDispose {
            stopped.set(true)
            analysis.clearAnalyzer()
            bound?.unbind(preview, analysis)
            worker.shutdown()
        }
    }
}

private fun decodeQr(reader: QRCodeReader, image: ImageProxy): String? {
    val plane = image.planes[0]
    val y = plane.buffer
    val stride = plane.rowStride
    val bytes = ByteArray(stride * image.height)
    y.get(bytes, 0, y.remaining())
    val side = (minOf(image.width, image.height) * 0.8).toInt()
    val source = PlanarYUVLuminanceSource(bytes, stride, image.height,
        (image.width - side) / 2, (image.height - side) / 2, side, side, false)
    return try {
        reader.decode(BinaryBitmap(HybridBinarizer(source))).text
    } catch (_: NotFoundException) {
        null // A camera frame without a complete QR code is expected.
    } catch (_: ChecksumException) {
        null
    } catch (_: FormatException) {
        null
    }
}
