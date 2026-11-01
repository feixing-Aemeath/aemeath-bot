package local.assistant.android.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.roundToInt

/** Uses the downloaded, authenticated local file; no URL or credential reaches a viewer. */
@Composable
internal fun OriginalPreview(file: File, pdf: Boolean, onClose: () -> Unit) {
    DisposableEffect(file) {
        onDispose { file.delete() }
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        AemeathTheme {
            PageFrame("原件", onClose) {
                if (pdf) PdfPreview(file) else PreviewImage(file, null)
            }
        }
    }
}

private fun renderer(file: File): PdfRenderer {
    val descriptor = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    return try {
        PdfRenderer(descriptor)
    } catch (error: Exception) {
        descriptor.close()
        throw error
    }
}

@Composable
private fun PdfPreview(file: File) {
    var pages by remember(file) { mutableStateOf<Int?>(null) }
    var failed by remember(file) { mutableStateOf(false) }
    LaunchedEffect(file) {
        try {
            pages = withContext(Dispatchers.IO) { renderer(file).use { it.pageCount } }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            failed = true
        }
    }
    if (failed) Muted("无法预览这份 PDF，文件可能已损坏或需要密码。", Modifier.padding(20.dp))
    else if (pages == null) Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
    else LazyColumn(contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        items((0 until checkNotNull(pages)).toList()) { page ->
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Muted("第 ${page + 1} / $pages 页")
                PreviewImage(file, page)
            }
        }
    }
}

@Composable
private fun PreviewImage(file: File, page: Int?) {
    var bitmap by remember(file, page) { mutableStateOf<Bitmap?>(null) }
    var failed by remember(file, page) { mutableStateOf(false) }
    var scale by remember(file, page) { mutableFloatStateOf(1f) }
    var offset by remember(file, page) { mutableStateOf(Offset.Zero) }
    val transform = rememberTransformableState { zoom, pan, _ ->
        scale = (scale * zoom).coerceIn(1f, 5f)
        offset = if (scale == 1f) Offset.Zero else offset + pan
    }
    LaunchedEffect(file, page) {
        try {
            bitmap = withContext(Dispatchers.IO) {
                if (page == null) {
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeFile(file.path, bounds)
                    val options = BitmapFactory.Options().apply {
                        inSampleSize = 1
                        while (maxOf(bounds.outWidth, bounds.outHeight) / inSampleSize > 2048) inSampleSize *= 2
                    }
                    checkNotNull(BitmapFactory.decodeFile(file.path, options))
                } else renderer(file).use { document ->
                    document.openPage(page).use { pdfPage ->
                        val ratio = minOf(2f, 2048f / maxOf(pdfPage.width, pdfPage.height))
                        Bitmap.createBitmap(
                            (pdfPage.width * ratio).roundToInt().coerceAtLeast(1),
                            (pdfPage.height * ratio).roundToInt().coerceAtLeast(1), Bitmap.Config.ARGB_8888
                        ).also { image ->
                            image.eraseColor(android.graphics.Color.WHITE)
                            pdfPage.render(image, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                        }
                    }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            failed = true
        }
    }
    Box(
        Modifier.then(if (page == null) Modifier.fillMaxSize() else Modifier.fillMaxWidth().heightIn(min = 160.dp))
            .clipToBounds().transformable(transform),
        contentAlignment = Alignment.Center
    ) {
        val image = bitmap
        if (image != null) Image(
            image.asImageBitmap(), contentDescription = if (page == null) "图片原件" else "PDF 第 ${page + 1} 页",
            contentScale = ContentScale.Fit,
            modifier = (if (page == null) Modifier.fillMaxSize() else Modifier.fillMaxWidth().aspectRatio(image.width.toFloat() / image.height))
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offset.x
                    translationY = offset.y
                }
        )
        else if (failed) Text("无法预览原件。")
        else CircularProgressIndicator()
    }
}
