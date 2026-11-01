package local.assistant.android.data

import android.content.Context
import android.os.Build
import android.util.DisplayMetrics
import android.view.WindowManager

object DeviceProfile {
    /** Use the full display, so splitting a tablet window doesn't turn on phone health sync. */
    fun isTablet(context: Context): Boolean {
        val manager = context.getSystemService(WindowManager::class.java)
        val density = context.resources.displayMetrics.density
        if (Build.VERSION.SDK_INT >= 30) {
            val bounds = manager.maximumWindowMetrics.bounds
            return minOf(bounds.width(), bounds.height()) / density >= 600
        }
        @Suppress("DEPRECATION")
        val metrics = DisplayMetrics().also { manager.defaultDisplay.getRealMetrics(it) }
        return minOf(metrics.widthPixels, metrics.heightPixels) / density >= 600
    }
}
