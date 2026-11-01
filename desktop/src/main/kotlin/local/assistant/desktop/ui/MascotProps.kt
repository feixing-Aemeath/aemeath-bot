package local.assistant.desktop.ui

import androidx.compose.ui.graphics.drawscope.DrawScope
import local.assistant.core.AnimationPlayback
import kotlin.math.*

// 240-unit canvas and coordinates follow MascotAnimationView.swift. No extra bitmap assets.
internal data class PropPlacement(val scale: Double, val x: Double, val y: Double) {

    fun point(x: Double, y: Double) = 240 * (this.x + (x - .5) * scale) to 240 * (this.y + (y - .5) * scale)
}

internal fun DrawScope.drawMascotProps(side: Float, playback: AnimationPlayback, placement: PropPlacement) {
    val canvas = PropCanvas(this)
    canvas.save()
    canvas.scale(side / 240, side / 240)
    canvas.saveLayerAlpha(0f, 0f, 240f, 240f, (playback.visibility * 255).toInt().coerceIn(0, 255))
    PropPainter(canvas, playback, placement).draw()
    canvas.restore()
    canvas.restore()
}

private class PropPainter(val c: PropCanvas, val playback: AnimationPlayback, val place: PropPlacement) {
    val t = playback.elapsed
    val p = playback.phase
    val wave = sin(p * 2 * PI)
    val ink = PropColor.rgb(48, 40, 64)
    val paper = PropColor.rgb(255, 250, 240)
    val gold = PropColor.rgb(255, 209, 102)
    val accent = PropColor.parseColor(when (playback.act.sticker) {
        "lily_veil" -> "#8068B2"
        "shouanren" -> "#3E7FD6"
        "red_ribbon" -> "#E8383F"
        "moning" -> "#D65F63"
        "luosela" -> "#5B43C9"
        else -> "#E86A9A"
    })
    val paint = PropPaint()

    fun fill(path: PropPath, color: Int, width: Double = 2.8) {
        paint.color = color
        paint.style = PropPaint.Style.FILL
        c.drawPath(path, paint)
        paint.color = ink
        paint.style = PropPaint.Style.STROKE
        paint.strokeWidth = width.toFloat()
        c.drawPath(path, paint)
    }

    fun box(x: Double, y: Double, w: Double, h: Double, color: Int, r: Double = 6.0) = fill(PropPath().apply {
        addRoundRect(x.toFloat(), y.toFloat(), (x + w).toFloat(), (y + h).toFloat(), r.toFloat(), r.toFloat())
    }, color)

    fun oval(x: Double, y: Double, w: Double, h: Double, color: Int, width: Double = 2.8) = fill(PropPath().apply {
        addOval(x.toFloat(), y.toFloat(), (x + w).toFloat(), (y + h).toFloat())
    }, color, width)

    fun line(vararg points: Pair<Double, Double>, color: Int = ink, width: Double = 2.5) {
        if (points.isEmpty()) return
        val path = PropPath().apply {
            moveTo(points[0].first.toFloat(), points[0].second.toFloat())
            points.drop(1).forEach {
                lineTo(it.first.toFloat(), it.second.toFloat())
            }
        }
        paint.style = PropPaint.Style.STROKE
        paint.color = color
        paint.strokeWidth = width.toFloat()
        c.drawPath(path, paint)
    }

    fun polygon(vararg points: Pair<Double, Double>, color: Int) {
        val path = PropPath()
        points.forEachIndexed {
            i, v -> if (i == 0) path.moveTo(v.first.toFloat(), v.second.toFloat()) else path.lineTo(v.first.toFloat(), v.second.toFloat())
        }
        path.close()
        fill(path, color)
    }

    fun text(value: String, x: Double, y: Double, size: Double, color: Int = ink) {
        paint.style = PropPaint.Style.FILL
        paint.color = color
        paint.textSize = size.toFloat()
        c.drawText(value, x.toFloat(), (y - (paint.fontMetrics.ascent + paint.fontMetrics.descent) / 2).toFloat(), paint)
    }

    fun star(x: Double, y: Double, size: Double, color: Int = gold) {
        val path = PropPath()
        repeat(8) { i ->
            val a = i * PI / 4 - PI / 2
            val r = size * if (i % 2 == 0) .5 else .17
            val xx = (x + cos(a) * r).toFloat()
            val yy = (y + sin(a) * r).toFloat()
            if (i == 0) path.moveTo(xx, yy) else path.lineTo(xx, yy)
        }
        path.close()
        fill(path, color, 1.8)
    }

    fun heart(x: Double, y: Double, size: Double) {
        c.save()
        c.translate(x.toFloat(), y.toFloat())
        c.scale((size / 40).toFloat(), (size / 40).toFloat())
        val path = PropPath().apply {
            moveTo(0f, 17f)
            cubicTo(-8f, 10f, -23f, 1f, -18f, -7f)
            cubicTo(-16f, -22f, -3f, -20f, 0f, -9f)
            cubicTo(3f, -20f, 16f, -22f, 18f, -7f)
            cubicTo(23f, 1f, 8f, 10f, 0f, 17f)
        }
        fill(path, accent)
        line(-12.0 to -8.0, -9.0 to -12.0, color = PropColor.WHITE, width = 3.0)
        c.restore()
    }

    fun check(x: Double, y: Double, progress: Double, size: Double = 13.0) {
        val path = PropPath().apply {
            moveTo(x.toFloat(), y.toFloat())
            lineTo((x + size * .35).toFloat(), (y + size * .35).toFloat())
            lineTo((x + size).toFloat(), (y - size * .4).toFloat())
        }
        val measure = PropPathMeasure(path)
        val trimmed = PropPath()
        measure.getSegment(0f, (measure.length * progress.coerceIn(0.0, 1.0)).toFloat(), trimmed, true)
        paint.style = PropPaint.Style.STROKE
        paint.color = accent
        paint.strokeWidth = 3.5f
        c.drawPath(trimmed, paint)
    }

    fun sheet(title: String, y: Double = 179.0) {
        box(68.0, y, 105.0, 55.0, paper)
        box(96.0, y - 5, 48.0, 10.0, accent, 4.0)
        text(title, 120.0, y + 12, 10.0)
    }

    fun hand(x: Double, y: Double, angle: Double) {
        c.save()
        c.translate(x.toFloat(), y.toFloat())
        c.rotate(angle.toFloat())
        val path = PropPath().apply {
            moveTo(-9f, 13f)
            lineTo(-12f, 0f)
            quadTo(-15f, -13f, -5f, -1f)
            lineTo(-5f, -17f)
            quadTo(-2f, -23f, 0f, -17f)
            quadTo(5f, -25f, 6f, -17f)
            quadTo(12f, -21f, 11f, -14f)
            lineTo(11f, 7f)
            quadTo(9f, 20f, -9f, 13f)
        }
        fill(path, PropColor.rgb(255, 230, 214), 2.3)
        box(-9.0, 10.0, 19.0, 9.0, accent, 3.0)
        c.restore()
    }

    fun pen(x: Double, y: Double, red: Boolean = false) {
        c.save()
        c.translate(x.toFloat(), y.toFloat())
        c.rotate(32f)
        box(-4.0, -42.0, 8.0, 34.0, if (red) accent else gold, 2.0)
        polygon(-4.0 to -8.0, 4.0 to -8.0, 0.0 to 0.0, color = paper)
        line(0.0 to -4.0, 0.0 to 0.0, width = 2.0)
        c.restore()
    }

    fun crescent(x: Double, y: Double, size: Double, color: Int = paper) {
        val path = PropPath().apply {
            moveTo((x + size * .2).toFloat(), (y - size * .5).toFloat())
            cubicTo((x - size * .75).toFloat(), (y - size * .5).toFloat(), (x - size * .75).toFloat(), (y + size * .5).toFloat(), (x + size * .2).toFloat(), (y + size * .5).toFloat())
            cubicTo((x - size * .22).toFloat(), (y + size * .25).toFloat(), (x - size * .22).toFloat(), (y - size * .25).toFloat(), (x + size * .2).toFloat(), (y - size * .5).toFloat())
        }
        fill(path, color, 1.8)
    }

    fun earrings() {
        listOf(.2 to .86, .68 to .92).forEachIndexed { i, xy ->
            val point = place.point(xy.first, xy.second)
            c.save()
            c.translate(point.first.toFloat(), point.second.toFloat())
            c.rotate((16 * sin(t * 5 + i * .5)).toFloat())
            oval(-3.0, -3.0, 6.0, 6.0, paper, 1.5)
            crescent(0.0, 10.0, 18.0)
            c.restore()
        }
    }

    fun draw() {
        when (playback.act.id) {
            "sleep" -> {
                val y = 236 - 45 * (1 - (1 - min(1.0, t / 1.1)).pow(3)) + 2 * sin(t * PI / 2)
                val quilt = PropPath().apply {
                    moveTo(28f, (y + 8).toFloat())
                    quadTo(120f, (y - 12).toFloat(), 212f, (y + 8).toFloat())
                    lineTo(217f, 234f)
                    quadTo(120f, 240f, 23f, 234f)
                    close()
                }
                fill(quilt, accent)
                repeat(13) { i ->
                    val x = 31 + i * 14.0
                    val top = y + 6 - 7 * sin(i / 12.0 * PI)
                    oval(x - 5, top - 4, 12.0, 12.0, paper, 1.4)
                    oval(x - 1, top - 1, 3.0, 3.0, accent, .7)
                }
                line(45.0 to y + 22, 49.0 to 229.0, color = PropColor.LTGRAY)
                star(159.0, y + 25, 13.0, paper)
                repeat(3) { i ->
                    val f = (t / 3 + i / 3.0) % 1
                    text("z", 187 + f * 22, 75 - f * 38, 12 + f * 10, accent)
                }
            }
            "heartbeat" -> {
                val beat = t % .9
                val pulse = exp(-((beat - .12) / .07).pow(2)) + .65 * exp(-((beat - .31) / .08).pow(2))
                heart(192.0, 163.0, 32 + 8 * pulse)
                box(62.0, 201.0, 116.0, 27.0, paper)
                line(72.0 to 215.0, 92.0 to 215.0, 100.0 to 210.0, 107.0 to 221.0, 117.0 to 205.0, 126.0 to 215.0, 167.0 to 215.0, color = accent)
                oval(72 + p * 92, 212.0, 5.0, 5.0, PropColor.WHITE, 1.0)
            }
            "stretch" -> {
                val reach = sin(p * PI)
                hand(36.0, 180 - 66 * reach, -25 - 15 * reach)
                hand(203.0, 180 - 66 * reach, 25 + 15 * reach)
                val mouth = place.point(.5, .88)
                oval(mouth.first - 4, mouth.second - 4, 8.0, 5 + 8 * reach, ink, 1.0)
                star(202.0, 52.0, 28 + 4 * reach)
            }
            "training" -> {
                val y = 207 - 13 * abs(wave)
                box(82.0, y - 4, 76.0, 8.0, paper, 3.0)
                listOf(79.0, 151.0).forEach {
                    box(it, y - 17, 12.0, 34.0, accent, 4.0)
                }
                hand(105.0, y, 0.0)
                hand(135.0, y, 0.0)
                star(49.0, 170 - 10 * p, 14.0)
                star(195.0, 160 - 10 * p, 18.0)
            }
            "tokens" -> {
                box(58.0, 22.0, 124.0, 38.0, accent)
                val count = 1280 + (t * 16).toInt()
                repeat(4) { i ->
                    val x = 66 + i * 28.0
                    box(x, 28.0, 24.0, 26.0, paper, 3.0)
                    val divisor = 10.0.pow(3 - i).toInt()
                    val roll = (t * 16 / divisor) % 1
                    c.save()
                    c.clipRect((x + 1).toFloat(), 29f, (x + 23).toFloat(), 53f)
                    text("${count/divisor%10}", x + 12, 41 - roll * 25, 20.0)
                    text("${(count/divisor+1)%10}", x + 12, 66 - roll * 25, 20.0)
                    c.restore()
                }
                text("TOKEN", 120.0, 72.0, 10.0, accent)
                repeat(3) { i ->
                    val y = 187 + i * 12.0
                    line(68.0 to y, 172.0 to y)
                    repeat(4) {
                        j -> oval(79 + j * 14 + 26 * (.5 + .5 * sin(t * 4 + i)), y - 5, 10.0, 10.0, accent, 1.5)
                    }
                }
            }
            "quota" -> {
                val path = PropPath().apply {
                    moveTo(63f, 222f)
                    arcTo(63f, 165f, 177f, 279f, 180f, 180f, false)
                    close()
                }
                fill(path, paper)
                repeat(9) { i ->
                    val a = PI + i * PI / 8
                    line(120 + cos(a) * 46 to 222 + sin(a) * 46, 120 + cos(a) * 51 to 222 + sin(a) * 51, color = accent)
                }
                val a = PI + (.15 + .7 * (.5 - .5 * cos(p * 2 * PI))) * PI
                line(120.0 to 219.0, 120 + cos(a) * 40 to 219 + sin(a) * 40, color = accent, width = 4.0)
                oval(115.0, 214.0, 10.0, 10.0, ink)
                text("额度", 95.0, 213.0, 9.0, accent)
            }
            "stars" -> listOf(.12 to .76, .19 to .69, .79 to .65, .89 to .8, .1 to .91).forEachIndexed { i, xy ->
                val point = place.point(xy.first, xy.second)
                star(point.first, point.second, 5 + 12 * (.5 + .5 * sin(t * 4 + i * 1.8)), PropColor.WHITE)
            }
            "savings" -> {
                repeat(3) { i ->
                    val rise = ((p - i * .12) * 4).coerceIn(0.0, 1.0)
                    val x = 91 + i * 29.0
                    val y = 226 - rise * (23 + i * 13)
                    oval(x - 13, y - 13, 26.0, 26.0, gold)
                    star(x, y, 14.0, accent)
                }
                star(194.0, 127 - p * 22, 18 * sin(p * PI))
                text("+1", 182.0, 170 - p * 22, 16.0, accent)
            }
            "grading" -> {
                sheet("小测验")
                repeat(2) { i ->
                    val y = 207 + i * 14.0
                    line(82.0 to y, 123.0 to y, color = PropColor.LTGRAY)
                    check(134.0, y, p * 4 - i)
                }
                if (p > .55) text("A+", 185.0, 174.0, 23.0, accent)
                pen(if (p < .55) 138 + 10 * sin(p * 20) else 188.0, if (p < .55) 191 + p * 40 else 176.0, true)
            }
            "ribbon" -> {
                val point = place.point(.245, .405)
                val bounce = sin(p * PI * 4) * exp(-p * 3)
                c.save()
                c.translate(point.first.toFloat(), point.second.toFloat())
                c.rotate((bounce * 16).toFloat())
                c.scale((1 + .14 * bounce).toFloat(), (1 - .12 * bounce).toFloat())
                listOf(-1f, 1f).forEach { sign ->
                    val path = PropPath().apply {
                        moveTo(0f, 0f)
                        cubicTo(sign * 20, -26f, sign * 41, -31f, sign * 36, -17f)
                        cubicTo(sign * 42, 16f, sign * 13, 14f, 0f, 0f)
                    }
                    fill(path, accent)
                }
                box(-6.0, -9.0, 12.0, 19.0, accent, 4.0)
                c.restore()
                star(point.first - 37, point.second - 30, 12 * abs(bounce))
            }
            "report" -> {
                val y = 234 - 55 * min(1.0, p * 4)
                sheet("成绩单", y)
                text("A+", 100.0, y + 35, 21.0, accent)
                repeat(3) {
                    i -> line(128.0 to y + 26 + i * 9, 158.0 to y + 26 + i * 9, color = accent)
                }
                hand(65.0, y + 35, -15.0)
                hand(175.0, y + 35, 15.0)
            }
            "medal" -> {
                val y = 192 - 10 * abs(wave)
                listOf(-1, 1).forEach {
                    sign -> polygon(120.0 to y, 120 + sign * 25.0 to y + 36, 120 + sign * 12.0 to y + 30, 120 + sign * 4.0 to y + 37, color = accent)
                }
                oval(97.0, y - 24, 46.0, 46.0, gold)
                star(120.0, y - 1, 27.0, paper)
                repeat(5) {
                    i -> star(49 + i * 36.0, 147 - 25 * sin(i + p * PI), 8.0)
                }
            }
            "writing" -> {
                box(56.0, 185.0, 128.0, 45.0, accent)
                box(61.0, 181.0, 118.0, 44.0, paper, 3.0)
                line(120.0 to 184.0, 120.0 to 220.0)
                repeat(3) { i ->
                    val y = 191 + i * 11.0
                    line(70.0 to y, 110.0 to y, color = PropColor.LTGRAY, width = 1.5)
                    val progress = (p * 3 - i).coerceIn(0.0, 1.0)
                    if (progress>0) line(129.0 to y, 129 + 40 * progress to y, color = accent, width = 2.0)
                }
                pen(129 + (p * 3 % 1) * 40, 191 + floor(p * 3) * 11 + sin(t * 24) * 2)
            }
            "books" -> {
                repeat(3) { i ->
                    val drop = (p * 4 - i * .8).coerceIn(0.0, 1.0)
                    if (drop>0) {
                        val x = 66 + (i % 2) * 9.0
                        val y = 214 - i * 18 - (1 - drop).pow(2) * 70
                        box(x, y, 105.0, 16.0, if (i == 1) gold else accent, 3.0)
                        box(x + 10, y + 4, 90.0, 8.0, paper, 1.0)
                    }
                }
                if (p > .65) star(183.0, 164.0, 22 * sin((p - .65) / .35 * PI))
            }
            "alarm" -> {
                c.save()
                c.translate(120f, 196f)
                c.rotate((9 * sin(t * 34)).toFloat())
                listOf(-22.0, 22.0).forEach {
                    x -> oval(x - 10, -33.0, 20.0, 14.0, accent)
                    line(x * .65 to 20.0, x to 32.0, width = 4.0)
                }
                oval(-29.0, -27.0, 58.0, 54.0, paper)
                line(0.0 to 0.0, 0.0 to -17.0)
                line(0.0 to 0.0, 13.0 to 5.0, color = accent, width = 3.0)
                c.restore()
                listOf(-1, 1).forEach {
                    sign -> line(120 + sign * 43.0 to 173.0, 120 + sign * (49 + 3 * wave) to 166.0, color = accent)
                    line(120 + sign * 46.0 to 190.0, 120 + sign * 57.0 to 188.0, color = accent)
                }
            }
            "halo" -> {
                val point = place.point(.41, .405)
                c.save()
                c.translate(point.first.toFloat(), point.second.toFloat())
                c.rotate(-12f)
                repeat(7) { i ->
                    val a = i / 7.0 * 2 * PI + p * 2 * PI
                    val x = cos(a) * 240 * place.scale * .235
                    val y = sin(a) * 240 * place.scale * .057
                    polygon(x to y - 5, x + 4 to y, x to y + 5, x - 4 to y, color = if (i % 2 == 0) PropColor.rgb(186, 179, 245) else paper)
                }
                c.restore()
            }
            "calendar" -> {
                box(77.0, 180.0, 86.0, 54.0, paper)
                box(77.0, 180.0, 86.0, 12.0, accent, 4.0)
                listOf(94.0, 146.0).forEach {
                    line(it to 175.0, it to 185.0, width = 4.0)
                }
                val page = (p * 4).toInt()
                text("${12+page}", 120.0, 214.0, 25.0)
                val flip = p * 4 % 1
                val h = 39 * cos(flip * PI / 2)
                box(79 - 15 * flip, 193 - 18 * flip, 82.0, max(1.0, h), paper, 3.0)
                if (flip < .6) text("${11+page}", 120 - 15 * flip, 193 - 18 * flip + h / 2, 24 * (1 - flip))
            }
            "approval" -> {
                sheet("请假条")
                line(81.0 to 203.0, 155.0 to 203.0, color = PropColor.LTGRAY)
                if (p > .44) {
                    c.save()
                    c.rotate(-9f, 123f, 218f)
                    box(91.0, 206.0, 64.0, 24.0, paper, 3.0)
                    text("已批准", 123.0, 218.0, 15.0, accent)
                    c.restore()
                }
                val drop = if (p < .45) min(1.0, p / .45) else max(0.0, 1 - (p - .45) / .3)
                val y = 149 + 60 * drop
                box(101.0, y - 17, 44.0, 14.0, accent, 3.0)
                box(115.0, y - 39, 16.0, 25.0, gold)
            }
            "meteor" -> {
                val target = place.point(.34, .43)
                val f = min(1.0, p / .65)
                val x = 207 + (target.first - 207) * f
                val y = 20 + (target.second - 20) * f
                if (f < 1) line(x + 24 to y - 17, x to y, color = gold, width = 4.0)
                star(x, y, if (f < 1) 16.0 else 16 + 16 * sin((p - .65) / .35 * PI))
                if (f == 1.0) {
                    star(target.first - 20, target.second + 3, 9.0, PropColor.WHITE)
                    star(target.first + 22, target.second - 8, 10.0, PropColor.WHITE)
                }
                earrings()
            }
            "earrings" -> earrings()
            "umbrella" -> {
                val opening = min(1.0, t / .75)
                c.save()
                c.translate(131f, 54f)
                line(0.0 to -19.0, 0.0 to 107.0, -5.0 to 113.0, -13.0 to 112.0, width = 3.2)
                c.scale((.12 + .88 * opening).toFloat(), (.65 + .35 * opening).toFloat())
                val canopy = PropPath().apply {
                    moveTo(-82f, 13f)
                    cubicTo(-67f, -64f, 67f, -64f, 82f, 13f)
                    for (i in 3 downTo 0) {
                        val x = -82 + i * 41f
                        quadTo(x + 20.5f, -3f, x, 13f)
                    }
                    close()
                }
                fill(canopy, accent)
                listOf(-41f, 0f, 41f).forEach { x ->
                    val rib = PropPath().apply {
                        moveTo(0f, -44f)
                        quadTo(x, -26f, x, 13f)
                    }
                    paint.color = ink
                    paint.style = PropPaint.Style.STROKE
                    paint.strokeWidth = 1.7f
                    c.drawPath(rib, paint)
                }
                c.restore()
                repeat(7) { i ->
                    val f = (t * .7 + i / 7.0) % 1
                    val x = if (i % 2 == 0) 23 + i * 2.0 else 205 + i.toDouble()
                    line(x to 52 + f * 130, x - 3 to 61 + f * 130, color = PropColor.rgb(102, 176, 230))
                }
            }
            "checklist" -> {
                sheet("今日待办")
                repeat(3) { i ->
                    val y = 201 + i * 12.0
                    box(81.0, y - 4, 9.0, 9.0, PropColor.WHITE, 2.0)
                    line(103.0 to y + 1, 158.0 to y + 1, color = PropColor.LTGRAY, width = 2.0)
                    check(81.0, y, p * 4 - i, 12.0)
                }
            }
            "wave" -> {
                hand(196.0, 145.0, 20 + 24 * sin(t * 10))
                repeat(2) { i ->
                    val y = 102 + i * 12.0
                    line(201.0 to y, 213 + 3 * wave to y - 4, color = accent)
                }
                heart(40.0, 117 - p * 25, 16 + 4 * sin(p * PI))
            }
            "moon" -> {
                val point = place.point(.73, .51)
                val glow = .5 + .5 * sin(t * 3)
                crescent(point.first, point.second, 29.0, PropColor.rgb((217 + 38 * glow).toInt(), 240, 255))
                star(point.first + 19, point.second - 17, 7 + 7 * glow, PropColor.WHITE)
                star(point.first - 20, point.second + 21, 12 - 6 * glow, PropColor.WHITE)
            }
        }
    }
}
