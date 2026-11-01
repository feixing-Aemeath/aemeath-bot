#if !os(watchOS)
    import AssistantCore
    import SwiftUI

    /// A complete square stage, shared by the live view and the offline preview renderer.
    /// All props use a 240-unit canvas, scaled once, including strokes and text.
    struct MascotAnimationScene: View {
        let name: String
        let frame: MascotFrame
        let playback: MascotAnimationPlayback?
        let side: CGFloat
        var artwork: LayeredImages?

        private var bounds: CGRect {
            artwork?.base.rect ?? StickerLayers.shared.bounds(named: name)
        }

        var body: some View {
            let placement = MascotStagePlacement(
                bounds: bounds, eyes: artwork?.open.rect ?? StickerLayers.shared.eyes(named: name))
            let acting = playback?.applying(to: frame) ?? frame
            let attached = playback?.animation.attached ?? false
            ZStack {
                ZStack {
                    MascotFigure(
                        name: name, pose: MascotPose(), side: side * placement.scale,
                        eyes: acting.eyes, artwork: artwork
                    )
                    .position(x: side * placement.center.x, y: side * placement.center.y)
                    if attached, let playback {
                        MascotProps(playback: playback, placement: placement)
                    }
                }
                .frame(width: side, height: side)
                .saturation(acting.pose.saturation)
                .scaleEffect(x: acting.pose.scaleX, y: acting.pose.scaleY, anchor: .bottom)
                .rotationEffect(.degrees(acting.pose.rotation), anchor: .bottom)
                .offset(x: acting.pose.offsetX * side, y: acting.pose.offsetY * side)
                if !attached, let playback {
                    MascotProps(playback: playback, placement: placement)
                }
                if playback?.animation != .sleep {
                    if acting.pose.effects.contains(.zzz) {
                        Text("Zzz").font(.system(size: side * 0.09, weight: .bold, design: .rounded))
                            .foregroundStyle(MascotPalette.sleep.color)
                            .position(x: side * 0.83, y: side * (0.17 - 0.06 * acting.pose.effectPhase))
                    }
                    if acting.pose.effects.contains(.sweat) {
                        Image(systemName: "drop.fill")
                            .font(.system(size: side * 0.1)).foregroundStyle(MascotPalette.sweat.color)
                            .position(x: side * 0.87, y: side * (0.35 + 0.08 * acting.pose.effectPhase))
                    }
                }
            }
            .frame(width: side, height: side)
            .allowsHitTesting(false)
        }
    }

    struct MascotStagePlacement {
        let scale: CGFloat
        let center: CGPoint

        /// With the eye layer's place, every face is the same size (like FacePlacement): the
        /// stickers were cut differently, and a hat or halo would otherwise shrink the face. Then
        /// the eyes sit on one line and the outline is centered across.
        init(bounds: CGRect, eyes: CGRect? = nil) {
            if let eyes {
                scale = min(0.29 / max(0.01, eyes.width), 0.94 / max(0.01, bounds.width))
                center = CGPoint(x: 0.5 + (0.5 - bounds.midX) * scale, y: 0.66 + (0.5 - eyes.midY) * scale)
            } else {
                scale = min(0.78 / max(0.01, bounds.width), 0.68 / max(0.01, bounds.height))
                center = CGPoint(x: 0.5 + (0.5 - bounds.midX) * scale, y: 0.87 + (0.5 - bounds.maxY) * scale)
            }
        }

        init(named name: String) {
            self.init(bounds: StickerLayers.shared.bounds(named: name), eyes: StickerLayers.shared.eyes(named: name))
        }

        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: 240 * (center.x + (x - 0.5) * scale), y: 240 * (center.y + (y - 0.5) * scale))
        }
    }

    private extension MascotAnimation {
        var attached: Bool {
            switch self {
            case .ribbon, .stars, .halo, .meteor, .earrings, .moon: return true
            default: return false
            }
        }
    }

    private struct MascotProps: View {
        let playback: MascotAnimationPlayback
        let placement: MascotStagePlacement

        var body: some View {
            Canvas { context, size in
                context.scaleBy(x: size.width / 240, y: size.height / 240)
                context.opacity = playback.visibility
                MascotPropPainter(playback: playback, placement: placement).draw(in: context)
            }
            .accessibilityHidden(true)
        }
    }

    private struct MascotPropPainter {
        let playback: MascotAnimationPlayback
        let placement: MascotStagePlacement
        let ink = Color(red: 48 / 255, green: 40 / 255, blue: 64 / 255)
        let paper = Color(red: 1, green: 0.98, blue: 0.94)
        let gold = Color(red: 1, green: 0.82, blue: 0.4)
        var accent: Color { playback.animation.accent.color }
        var t: Double { playback.elapsed }
        var p: Double { playback.phase }
        var wave: Double { sin(p * 2 * .pi) }

        func draw(in context: GraphicsContext) {
            switch playback.animation {
            case .sleep: blanket(context)
            case .heartbeat: heartbeat(context)
            case .stretch: stretch(context)
            case .training: training(context)
            case .tokens: tokens(context)
            case .quota: quota(context)
            case .stars: stars(context)
            case .savings: savings(context)
            case .grading: grading(context)
            case .ribbon: ribbon(context)
            case .report: report(context)
            case .medal: medal(context)
            case .writing: writing(context)
            case .books: books(context)
            case .alarm: alarm(context)
            case .halo: halo(context)
            case .calendar: calendar(context)
            case .approval: approval(context)
            case .meteor: meteor(context)
            case .earrings: earrings(context)
            case .umbrella: umbrella(context)
            case .checklist: checklist(context)
            case .wave: greeting(context)
            case .moon: moon(context)
            }
        }

        func fill(_ path: Path, _ color: Color, in c: GraphicsContext, width: CGFloat = 2.8) {
            c.fill(path, with: .color(color))
            c.stroke(path, with: .color(ink), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
        }

        func box(_ rect: CGRect, _ color: Color, in c: GraphicsContext, radius: CGFloat = 6) {
            fill(Path(roundedRect: rect, cornerRadius: radius), color, in: c)
        }

        func line(_ points: [CGPoint], in c: GraphicsContext, color: Color? = nil, width: CGFloat = 2.5) {
            guard let first = points.first else { return }
            var path = Path()
            path.move(to: first)
            for point in points.dropFirst() { path.addLine(to: point) }
            c.stroke(path, with: .color(color ?? ink), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round))
        }

        func text(_ value: String, at point: CGPoint, size: CGFloat, in c: GraphicsContext, color: Color? = nil) {
            c.draw(Text(value).font(.system(size: size, weight: .bold, design: .rounded)).monospacedDigit()
                .foregroundColor(color ?? ink), at: point)
        }

        func symbol(_ name: String, at point: CGPoint, size: CGFloat, in c: GraphicsContext, color: Color) {
            var image = c.resolve(Image(systemName: name))
            image.shading = .color(color)
            c.draw(image, in: CGRect(x: point.x - size / 2, y: point.y - size / 2, width: size, height: size))
        }

        func star(at point: CGPoint, size: CGFloat, in c: GraphicsContext, color: Color? = nil) {
            var path = Path()
            for i in 0..<8 {
                let angle = Double(i) * .pi / 4 - .pi / 2
                let r = size * (i.isMultiple(of: 2) ? 0.5 : 0.17)
                let v = CGPoint(x: point.x + CGFloat(cos(angle)) * r, y: point.y + CGFloat(sin(angle)) * r)
                if i == 0 { path.move(to: v) } else { path.addLine(to: v) }
            }
            path.closeSubpath()
            fill(path, color ?? gold, in: c, width: 1.8)
        }

        func heart(at point: CGPoint, size: CGFloat, in c: GraphicsContext) {
            var d = c
            d.translateBy(x: point.x, y: point.y)
            d.scaleBy(x: size / 40, y: size / 40)
            var path = Path()
            path.move(to: CGPoint(x: 0, y: 17))
            path.addCurve(to: CGPoint(x: -18, y: -7), control1: CGPoint(x: -8, y: 10), control2: CGPoint(x: -23, y: 1))
            path.addCurve(to: CGPoint(x: 0, y: -9), control1: CGPoint(x: -16, y: -22), control2: CGPoint(x: -3, y: -20))
            path.addCurve(to: CGPoint(x: 18, y: -7), control1: CGPoint(x: 3, y: -20), control2: CGPoint(x: 16, y: -22))
            path.addCurve(to: CGPoint(x: 0, y: 17), control1: CGPoint(x: 23, y: 1), control2: CGPoint(x: 8, y: 10))
            fill(path, accent, in: d)
            line([CGPoint(x: -12, y: -8), CGPoint(x: -9, y: -12)], in: d, color: .white, width: 3)
        }

        func check(at point: CGPoint, progress: Double, in c: GraphicsContext, size: CGFloat = 13) {
            let points = [point, CGPoint(x: point.x + size * 0.35, y: point.y + size * 0.35),
                          CGPoint(x: point.x + size, y: point.y - size * 0.4)]
            var path = Path()
            path.move(to: points[0])
            path.addLines(points)
            c.stroke(path.trimmedPath(from: 0, to: min(1, max(0, progress))), with: .color(accent),
                     style: StrokeStyle(lineWidth: 3.5, lineCap: .round, lineJoin: .round))
        }

        func sheet(in c: GraphicsContext, title: String, y: CGFloat = 179) {
            box(CGRect(x: 68, y: y, width: 105, height: 55), paper, in: c)
            box(CGRect(x: 96, y: y - 5, width: 48, height: 10), accent, in: c, radius: 4)
            text(title, at: CGPoint(x: 120, y: y + 12), size: 10, in: c)
        }

        func blanket(_ c: GraphicsContext) {
            let lift = min(1, t / 1.1)
            let y = 236 - 45 * (1 - pow(1 - lift, 3)) + 2 * sin(t * .pi / 2)
            var quilt = Path()
            quilt.move(to: CGPoint(x: 28, y: y + 8))
            quilt.addQuadCurve(to: CGPoint(x: 212, y: y + 8), control: CGPoint(x: 120, y: y - 12))
            quilt.addLine(to: CGPoint(x: 217, y: 234))
            quilt.addQuadCurve(to: CGPoint(x: 23, y: 234), control: CGPoint(x: 120, y: 240))
            quilt.closeSubpath()
            fill(quilt, accent, in: c)
            for i in 0..<13 {
                let x = 31 + Double(i) * 14
                let top = y + 6 - 7 * sin(Double(i) / 12 * .pi)
                fill(Path(ellipseIn: CGRect(x: x - 5, y: top - 4, width: 12, height: 12)), paper, in: c, width: 1.4)
                fill(Path(ellipseIn: CGRect(x: x - 1, y: top - 1, width: 3, height: 3)), accent, in: c, width: 0.7)
            }
            line([CGPoint(x: 45, y: y + 22), CGPoint(x: 49, y: 229)], in: c, color: ink.opacity(0.25))
            star(at: CGPoint(x: 159, y: y + 25), size: 13, in: c, color: paper)
            for i in 0..<3 {
                let f = (t / 3 + Double(i) / 3).truncatingRemainder(dividingBy: 1)
                var z = c
                z.opacity *= (1 - f) * min(1, t)
                text("z", at: CGPoint(x: 187 + f * 22, y: 75 - f * 38), size: 12 + f * 10, in: z, color: accent)
            }
        }

        func heartbeat(_ c: GraphicsContext) {
            let beat = t.truncatingRemainder(dividingBy: 0.9)
            let pulse = exp(-pow((beat - 0.12) / 0.07, 2)) + 0.65 * exp(-pow((beat - 0.31) / 0.08, 2))
            heart(at: CGPoint(x: 192, y: 163), size: 32 + 8 * pulse, in: c)
            box(CGRect(x: 62, y: 201, width: 116, height: 27), paper, in: c)
            let points = [CGPoint(x: 72, y: 215), CGPoint(x: 92, y: 215), CGPoint(x: 100, y: 210),
                          CGPoint(x: 107, y: 221), CGPoint(x: 117, y: 205), CGPoint(x: 126, y: 215), CGPoint(x: 167, y: 215)]
            line(points, in: c, color: accent)
            fill(Path(ellipseIn: CGRect(x: 72 + p * 92, y: 212, width: 5, height: 5)), .white, in: c, width: 1)
        }

        func hand(at point: CGPoint, angle: Double, in c: GraphicsContext) {
            var d = c
            d.translateBy(x: point.x, y: point.y)
            d.rotate(by: .degrees(angle))
            var path = Path()
            path.move(to: CGPoint(x: -9, y: 13))
            path.addLine(to: CGPoint(x: -12, y: 0))
            path.addQuadCurve(to: CGPoint(x: -5, y: -1), control: CGPoint(x: -15, y: -13))
            path.addLine(to: CGPoint(x: -5, y: -17))
            path.addQuadCurve(to: CGPoint(x: 0, y: -17), control: CGPoint(x: -2, y: -23))
            path.addQuadCurve(to: CGPoint(x: 6, y: -17), control: CGPoint(x: 5, y: -25))
            path.addQuadCurve(to: CGPoint(x: 11, y: -14), control: CGPoint(x: 12, y: -21))
            path.addLine(to: CGPoint(x: 11, y: 7))
            path.addQuadCurve(to: CGPoint(x: -9, y: 13), control: CGPoint(x: 9, y: 20))
            fill(path, Color(red: 1, green: 0.9, blue: 0.84), in: d, width: 2.3)
            box(CGRect(x: -9, y: 10, width: 19, height: 9), accent, in: d, radius: 3)
        }

        func stretch(_ c: GraphicsContext) {
            let reach = sin(p * .pi)
            hand(at: CGPoint(x: 36, y: 180 - 66 * reach), angle: -25 - 15 * reach, in: c)
            hand(at: CGPoint(x: 203, y: 180 - 66 * reach), angle: 25 + 15 * reach, in: c)
            let mouth = placement.point(0.5, 0.88)
            fill(Path(ellipseIn: CGRect(x: mouth.x - 4, y: mouth.y - 4, width: 8, height: 5 + 8 * reach)), ink, in: c, width: 1)
            symbol("sun.max.fill", at: CGPoint(x: 202, y: 52), size: 24 + 4 * reach, in: c, color: gold)
        }

        func training(_ c: GraphicsContext) {
            let y = 207 - 13 * abs(wave)
            box(CGRect(x: 82, y: y - 4, width: 76, height: 8), paper, in: c, radius: 3)
            for x in [79.0, 151.0] {
                box(CGRect(x: x, y: y - 17, width: 12, height: 34), accent, in: c, radius: 4)
            }
            hand(at: CGPoint(x: 105, y: y), angle: 0, in: c)
            hand(at: CGPoint(x: 135, y: y), angle: 0, in: c)
            star(at: CGPoint(x: 49, y: 170 - 10 * p), size: 14, in: c)
            star(at: CGPoint(x: 195, y: 160 - 10 * p), size: 18, in: c)
        }

        func tokens(_ c: GraphicsContext) {
            box(CGRect(x: 58, y: 22, width: 124, height: 38), accent, in: c)
            let count = 1280 + Int(t * 16)
            for i in 0..<4 {
                let x = 66 + Double(i) * 28
                box(CGRect(x: x, y: 28, width: 24, height: 26), paper, in: c, radius: 3)
                let divisor = Int(pow(10, Double(3 - i)))
                let value = count / divisor % 10
                let roll = (t * 16 / Double(divisor)).truncatingRemainder(dividingBy: 1)
                var d = c
                d.clip(to: Path(CGRect(x: x + 1, y: 29, width: 22, height: 24)))
                text("\(value)", at: CGPoint(x: x + 12, y: 41 - roll * 25), size: 20, in: d)
                text("\((value + 1) % 10)", at: CGPoint(x: x + 12, y: 66 - roll * 25), size: 20, in: d)
            }
            text("TOKEN", at: CGPoint(x: 120, y: 72), size: 10, in: c, color: accent)
            for i in 0..<3 {
                let y = 187 + Double(i) * 12
                line([CGPoint(x: 68, y: y), CGPoint(x: 172, y: y)], in: c)
                for j in 0..<4 {
                    let x = 79 + Double(j) * 14 + 26 * (0.5 + 0.5 * sin(t * 4 + Double(i)))
                    fill(Path(ellipseIn: CGRect(x: x, y: y - 5, width: 10, height: 10)), accent, in: c, width: 1.5)
                }
            }
        }

        func quota(_ c: GraphicsContext) {
            var arc = Path()
            arc.move(to: CGPoint(x: 63, y: 222))
            arc.addArc(center: CGPoint(x: 120, y: 222), radius: 57, startAngle: .degrees(180), endAngle: .degrees(0), clockwise: false)
            arc.closeSubpath()
            fill(arc, paper, in: c)
            for i in 0...8 {
                let a = .pi + Double(i) * .pi / 8
                line([CGPoint(x: 120 + cos(a) * 46, y: 222 + sin(a) * 46),
                      CGPoint(x: 120 + cos(a) * 51, y: 222 + sin(a) * 51)], in: c, color: accent)
            }
            let a = .pi + (0.15 + 0.7 * (0.5 - 0.5 * cos(p * 2 * .pi))) * .pi
            line([CGPoint(x: 120, y: 219), CGPoint(x: 120 + cos(a) * 40, y: 219 + sin(a) * 40)], in: c, color: accent, width: 4)
            fill(Path(ellipseIn: CGRect(x: 115, y: 214, width: 10, height: 10)), ink, in: c)
            text("额度", at: CGPoint(x: 95, y: 213), size: 9, in: c, color: accent)
        }

        func stars(_ c: GraphicsContext) {
            for (i, pair) in [(0.12, 0.76), (0.19, 0.69), (0.79, 0.65), (0.89, 0.8), (0.1, 0.91)].enumerated() {
                let glow = 0.5 + 0.5 * sin(t * 4 + Double(i) * 1.8)
                star(at: placement.point(pair.0, pair.1), size: 5 + 12 * glow, in: c, color: .white)
            }
        }

        func savings(_ c: GraphicsContext) {
            for i in 0..<3 {
                let rise = min(1, max(0, (p - Double(i) * 0.12) * 4))
                let x = 91 + Double(i) * 29
                let y = 226 - rise * (23 + Double(i) * 13)
                fill(Path(ellipseIn: CGRect(x: x - 13, y: y - 13, width: 26, height: 26)), gold, in: c)
                star(at: CGPoint(x: x, y: y), size: 14, in: c, color: accent)
            }
            star(at: CGPoint(x: 194, y: 127 - p * 22), size: 18 * sin(p * .pi), in: c)
            text("+1", at: CGPoint(x: 182, y: 170 - p * 22), size: 16, in: c, color: accent)
        }

        func pen(at point: CGPoint, in c: GraphicsContext, red: Bool = false) {
            var d = c
            d.translateBy(x: point.x, y: point.y)
            d.rotate(by: .degrees(32))
            box(CGRect(x: -4, y: -42, width: 8, height: 34), red ? accent : gold, in: d, radius: 2)
            var tip = Path()
            tip.addLines([CGPoint(x: -4, y: -8), CGPoint(x: 4, y: -8), .zero])
            tip.closeSubpath()
            fill(tip, paper, in: d, width: 2)
            line([CGPoint(x: 0, y: -4), .zero], in: d, width: 2)
        }

        func grading(_ c: GraphicsContext) {
            sheet(in: c, title: "小测验")
            for i in 0..<2 {
                let y = 207 + Double(i) * 14
                line([CGPoint(x: 82, y: y), CGPoint(x: 123, y: y)], in: c, color: ink.opacity(0.25))
                check(at: CGPoint(x: 134, y: y), progress: p * 4 - Double(i), in: c)
            }
            if p > 0.55 {
                var d = c
                d.opacity *= min(1, (p - 0.55) * 7)
                text("A+", at: CGPoint(x: 185, y: 174), size: 23, in: d, color: accent)
            }
            pen(at: CGPoint(x: p < 0.55 ? 138 + 10 * sin(p * 20) : 188, y: p < 0.55 ? 191 + p * 40 : 176), in: c, red: true)
        }

        func ribbon(_ c: GraphicsContext) {
            let center = placement.point(0.245, 0.405)
            var d = c
            d.translateBy(x: center.x, y: center.y)
            let bounce = sin(p * .pi * 4) * exp(-p * 3)
            d.rotate(by: .degrees(bounce * 16))
            d.scaleBy(x: 1 + 0.14 * bounce, y: 1 - 0.12 * bounce)
            for sign in [-1.0, 1.0] {
                var loop = Path()
                loop.move(to: .zero)
                loop.addCurve(to: CGPoint(x: sign * 36, y: -17), control1: CGPoint(x: sign * 20, y: -26), control2: CGPoint(x: sign * 41, y: -31))
                loop.addCurve(to: .zero, control1: CGPoint(x: sign * 42, y: 16), control2: CGPoint(x: sign * 13, y: 14))
                fill(loop, accent, in: d)
                line([CGPoint(x: sign * 12, y: -8), CGPoint(x: sign * 26, y: -13)], in: d, color: .white.opacity(0.7))
            }
            box(CGRect(x: -6, y: -9, width: 12, height: 19), accent, in: d, radius: 4)
            star(at: CGPoint(x: center.x - 37, y: center.y - 30), size: 12 * abs(bounce), in: c)
        }

        func report(_ c: GraphicsContext) {
            let y = 234 - 55 * min(1, p * 4)
            sheet(in: c, title: "成绩单", y: y)
            text("A+", at: CGPoint(x: 100, y: y + 35), size: 21, in: c, color: accent)
            for i in 0..<3 {
                line([CGPoint(x: 128, y: y + 26 + Double(i) * 9), CGPoint(x: 158, y: y + 26 + Double(i) * 9)], in: c, color: accent.opacity(0.5))
            }
            hand(at: CGPoint(x: 65, y: y + 35), angle: -15, in: c)
            hand(at: CGPoint(x: 175, y: y + 35), angle: 15, in: c)
        }

        func medal(_ c: GraphicsContext) {
            let y = 192 - 10 * abs(wave)
            for sign in [-1.0, 1.0] {
                var tail = Path()
                tail.addLines([CGPoint(x: 120, y: y), CGPoint(x: 120 + sign * 25, y: y + 36),
                               CGPoint(x: 120 + sign * 12, y: y + 30), CGPoint(x: 120 + sign * 4, y: y + 37)])
                tail.closeSubpath()
                fill(tail, accent, in: c)
            }
            fill(Path(ellipseIn: CGRect(x: 97, y: y - 24, width: 46, height: 46)), gold, in: c)
            star(at: CGPoint(x: 120, y: y - 1), size: 27, in: c, color: paper)
            for i in 0..<5 {
                star(at: CGPoint(x: 49 + Double(i) * 36, y: 147 - 25 * sin(Double(i) + p * .pi)), size: 8, in: c)
            }
        }

        func writing(_ c: GraphicsContext) {
            box(CGRect(x: 56, y: 185, width: 128, height: 45), accent, in: c)
            box(CGRect(x: 61, y: 181, width: 118, height: 44), paper, in: c, radius: 3)
            line([CGPoint(x: 120, y: 184), CGPoint(x: 120, y: 220)], in: c)
            for i in 0..<3 {
                let y = 191 + Double(i) * 11
                line([CGPoint(x: 70, y: y), CGPoint(x: 110, y: y)], in: c, color: ink.opacity(0.2), width: 1.5)
                let progress = min(1, max(0, p * 3 - Double(i)))
                if progress > 0 {
                    line([CGPoint(x: 129, y: y), CGPoint(x: 129 + 40 * progress, y: y)], in: c, color: accent, width: 2)
                }
            }
            pen(at: CGPoint(x: 129 + (p * 3).truncatingRemainder(dividingBy: 1) * 40,
                            y: 191 + floor(p * 3) * 11 + sin(t * 24) * 2), in: c)
        }

        func books(_ c: GraphicsContext) {
            for i in 0..<3 {
                let drop = min(1, max(0, p * 4 - Double(i) * 0.8))
                if drop > 0 {
                    let x = 66 + Double(i % 2) * 9
                    let y = 214 - Double(i) * 18 - pow(1 - drop, 2) * 70
                    box(CGRect(x: x, y: y, width: 105, height: 16), i == 1 ? gold : accent, in: c, radius: 3)
                    box(CGRect(x: x + 10, y: y + 4, width: 90, height: 8), paper, in: c, radius: 1)
                }
            }
            if p > 0.65 { star(at: CGPoint(x: 183, y: 164), size: 22 * sin((p - 0.65) / 0.35 * .pi), in: c) }
        }

        func alarm(_ c: GraphicsContext) {
            var d = c
            d.translateBy(x: 120, y: 196)
            d.rotate(by: .degrees(9 * sin(t * 34)))
            for x in [-22.0, 22.0] {
                fill(Path(ellipseIn: CGRect(x: x - 10, y: -33, width: 20, height: 14)), accent, in: d)
                line([CGPoint(x: x * 0.65, y: 20), CGPoint(x: x, y: 32)], in: d, width: 4)
            }
            fill(Path(ellipseIn: CGRect(x: -29, y: -27, width: 58, height: 54)), paper, in: d)
            line([.zero, CGPoint(x: 0, y: -17)], in: d)
            line([.zero, CGPoint(x: 13, y: 5)], in: d, color: accent, width: 3)
            for sign in [-1.0, 1.0] {
                line([CGPoint(x: 120 + sign * 43, y: 173), CGPoint(x: 120 + sign * (49 + 3 * wave), y: 166)], in: c, color: accent)
                line([CGPoint(x: 120 + sign * 46, y: 190), CGPoint(x: 120 + sign * 57, y: 188)], in: c, color: accent)
            }
        }

        func halo(_ c: GraphicsContext) {
            let center = placement.point(0.41, 0.405)
            let rx = 240 * placement.scale * 0.235
            let ry = 240 * placement.scale * 0.057
            var d = c
            d.translateBy(x: center.x, y: center.y)
            d.rotate(by: .degrees(-12))
            for i in 0..<7 {
                let a = Double(i) / 7 * 2 * .pi + p * 2 * .pi
                let point = CGPoint(x: CGFloat(cos(a)) * rx, y: CGFloat(sin(a)) * ry)
                var crystal = Path()
                crystal.addLines([CGPoint(x: point.x, y: point.y - 5), CGPoint(x: point.x + 4, y: point.y),
                                  CGPoint(x: point.x, y: point.y + 5), CGPoint(x: point.x - 4, y: point.y)])
                crystal.closeSubpath()
                fill(crystal, i.isMultiple(of: 2) ? Color(red: 0.73, green: 0.7, blue: 0.96) : paper, in: d, width: 1.3)
                line([CGPoint(x: point.x, y: point.y - 3), CGPoint(x: point.x - 2, y: point.y)], in: d, color: .white, width: 1)
            }
        }

        func calendar(_ c: GraphicsContext) {
            box(CGRect(x: 77, y: 180, width: 86, height: 54), paper, in: c)
            box(CGRect(x: 77, y: 180, width: 86, height: 12), accent, in: c, radius: 4)
            for x in [94.0, 146.0] { line([CGPoint(x: x, y: 175), CGPoint(x: x, y: 185)], in: c, width: 4) }
            let page = Int(p * 4)
            text("\(12 + page)", at: CGPoint(x: 120, y: 214), size: 25, in: c)
            let flip = (p * 4).truncatingRemainder(dividingBy: 1)
            var d = c
            d.opacity *= 1 - flip
            let height = 39 * cos(flip * .pi / 2)
            box(CGRect(x: 79 - 15 * flip, y: 193 - 18 * flip, width: 82, height: max(1, height)), paper, in: d, radius: 3)
            if flip < 0.6 { text("\(11 + page)", at: CGPoint(x: 120 - 15 * flip, y: 193 - 18 * flip + height / 2), size: 24 * (1 - flip), in: d) }
        }

        func approval(_ c: GraphicsContext) {
            sheet(in: c, title: "请假条")
            line([CGPoint(x: 81, y: 203), CGPoint(x: 155, y: 203)], in: c, color: ink.opacity(0.25))
            if p > 0.44 {
                var d = c
                d.translateBy(x: 123, y: 218)
                d.rotate(by: .degrees(-9))
                let rect = Path(roundedRect: CGRect(x: -32, y: -12, width: 64, height: 24), cornerRadius: 3)
                d.stroke(rect, with: .color(accent), lineWidth: 2.5)
                text("已批准", at: .zero, size: 15, in: d, color: accent)
            }
            let drop = p < 0.45 ? min(1, p / 0.45) : max(0, 1 - (p - 0.45) / 0.3)
            var d = c
            d.opacity *= p > 0.75 ? max(0, (1 - p) * 4) : 1
            let y = 149 + 60 * drop
            box(CGRect(x: 101, y: y - 17, width: 44, height: 14), accent, in: d, radius: 3)
            box(CGRect(x: 115, y: y - 39, width: 16, height: 25), gold, in: d)
            if abs(p - 0.45) < 0.12 {
                for sign in [-1.0, 1.0] { line([CGPoint(x: 123 + sign * 34, y: 204), CGPoint(x: 123 + sign * 43, y: 196)], in: c, color: accent) }
            }
        }

        func meteor(_ c: GraphicsContext) {
            let target = placement.point(0.34, 0.43)
            let f = min(1, p / 0.65)
            let point = CGPoint(x: 207 + (target.x - 207) * f, y: 20 + (target.y - 20) * f)
            if f < 1 {
                line([CGPoint(x: point.x + 24, y: point.y - 17), point], in: c, color: gold, width: 4)
                line([CGPoint(x: point.x + 15, y: point.y - 21), CGPoint(x: point.x - 3, y: point.y - 5)], in: c, color: .white, width: 2)
            }
            star(at: point, size: f < 1 ? 16 : 16 + 16 * sin((p - 0.65) / 0.35 * .pi), in: c)
            if f == 1 {
                star(at: CGPoint(x: target.x - 20, y: target.y + 3), size: 9, in: c, color: .white)
                star(at: CGPoint(x: target.x + 22, y: target.y - 8), size: 10, in: c, color: .white)
            }
            earrings(c)
        }

        func crescent(at point: CGPoint, size: CGFloat, in c: GraphicsContext, color: Color) {
            var path = Path()
            path.move(to: CGPoint(x: point.x + size * 0.2, y: point.y - size * 0.5))
            path.addCurve(to: CGPoint(x: point.x + size * 0.2, y: point.y + size * 0.5),
                          control1: CGPoint(x: point.x - size * 0.75, y: point.y - size * 0.5),
                          control2: CGPoint(x: point.x - size * 0.75, y: point.y + size * 0.5))
            path.addCurve(to: CGPoint(x: point.x + size * 0.2, y: point.y - size * 0.5),
                          control1: CGPoint(x: point.x - size * 0.22, y: point.y + size * 0.25),
                          control2: CGPoint(x: point.x - size * 0.22, y: point.y - size * 0.25))
            fill(path, color, in: c, width: 1.8)
        }

        func earrings(_ c: GraphicsContext) {
            for (i, pair) in [(0.2, 0.86), (0.68, 0.92)].enumerated() {
                let point = placement.point(pair.0, pair.1)
                var d = c
                d.translateBy(x: point.x, y: point.y)
                d.rotate(by: .degrees(16 * sin(t * 5 + Double(i) * 0.5)))
                fill(Path(ellipseIn: CGRect(x: -3, y: -3, width: 6, height: 6)), paper, in: d, width: 1.5)
                crescent(at: CGPoint(x: 0, y: 10), size: 18, in: d, color: paper)
            }
        }

        func umbrella(_ c: GraphicsContext) {
            let open = min(1, t / 0.75)
            var d = c
            d.translateBy(x: 131, y: 54)
            line([CGPoint(x: 0, y: -19), CGPoint(x: 0, y: 107), CGPoint(x: -5, y: 113), CGPoint(x: -13, y: 112)], in: d, width: 3.2)
            d.scaleBy(x: 0.12 + 0.88 * open, y: 0.65 + 0.35 * open)
            var canopy = Path()
            canopy.move(to: CGPoint(x: -82, y: 13))
            canopy.addCurve(to: CGPoint(x: 82, y: 13), control1: CGPoint(x: -67, y: -64), control2: CGPoint(x: 67, y: -64))
            for i in (0..<4).reversed() {
                let x = -82 + Double(i) * 41
                canopy.addQuadCurve(to: CGPoint(x: x, y: 13), control: CGPoint(x: x + 20.5, y: -3))
            }
            canopy.closeSubpath()
            fill(canopy, accent, in: d)
            for x in [-41.0, 0, 41.0] {
                var rib = Path()
                rib.move(to: CGPoint(x: 0, y: -44))
                rib.addQuadCurve(to: CGPoint(x: x, y: 13), control: CGPoint(x: x, y: -26))
                d.stroke(rib, with: .color(ink), lineWidth: 1.7)
            }
            line([CGPoint(x: -54, y: -13), CGPoint(x: -43, y: -25)], in: d, color: .white, width: 3)
            for i in 0..<7 {
                let f = (t * 0.7 + Double(i) / 7).truncatingRemainder(dividingBy: 1)
                let x = i.isMultiple(of: 2) ? 23 + Double(i) * 2 : 205 + Double(i)
                line([CGPoint(x: x, y: 52 + f * 130), CGPoint(x: x - 3, y: 61 + f * 130)], in: c, color: Color(red: 0.4, green: 0.69, blue: 0.9), width: 2.5)
            }
        }

        func checklist(_ c: GraphicsContext) {
            sheet(in: c, title: "今日待办")
            for i in 0..<3 {
                let y = 201 + Double(i) * 12
                box(CGRect(x: 81, y: y - 4, width: 9, height: 9), .white, in: c, radius: 2)
                line([CGPoint(x: 103, y: y + 1), CGPoint(x: 158, y: y + 1)], in: c, color: ink.opacity(0.3), width: 2)
                check(at: CGPoint(x: 81, y: y), progress: p * 4 - Double(i), in: c, size: 12)
            }
        }

        func greeting(_ c: GraphicsContext) {
            hand(at: CGPoint(x: 196, y: 145), angle: 20 + 24 * sin(t * 10), in: c)
            for i in 0..<2 {
                let y = 102 + Double(i) * 12
                line([CGPoint(x: 201, y: y), CGPoint(x: 213 + 3 * wave, y: y - 4)], in: c, color: accent)
            }
            heart(at: CGPoint(x: 40, y: 117 - p * 25), size: 16 + 4 * sin(p * .pi), in: c)
        }

        func moon(_ c: GraphicsContext) {
            let point = placement.point(0.73, 0.51)
            let glow = 0.5 + 0.5 * sin(t * 3)
            var d = c
            d.opacity *= 0.18 + 0.12 * glow
            d.fill(Path(ellipseIn: CGRect(x: point.x - 23, y: point.y - 26, width: 46, height: 52)), with: .color(gold))
            crescent(at: point, size: 29, in: c, color: Color(red: 0.85 + 0.15 * glow, green: 0.94, blue: 1))
            star(at: CGPoint(x: point.x + 19, y: point.y - 17), size: 7 + 7 * glow, in: c, color: .white)
            star(at: CGPoint(x: point.x - 20, y: point.y + 21), size: 12 - 6 * glow, in: c, color: .white)
        }
    }
#endif
