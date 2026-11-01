import ImageIO
import XCTest

@testable import AssistantCore

final class MascotTests: XCTestCase {
    let now = Date(timeIntervalSince1970: 1_791_536_400)

    func run(_ status: RunStatus, job: String = "extract_todos", finishedAgo: TimeInterval? = nil)
        -> Run
    {
        Run(
            id: UUID().uuidString, job: job, harness: "claude_code",
            startedAt: now.addingTimeInterval(-60),
            finishedAt: finishedAgo.map { now.addingTimeInterval(-$0) }, status: status, error: nil,
            attempt: 1)
    }

    func plugin(_ state: PluginState, id: String = "feishu", mascot: PluginMascot? = nil)
        -> PluginView
    {
        PluginView(
            id: id, name: id, version: "1", icon: "message", description: "", platforms: ["any"],
            permissions: [], schedule: nil, provides: ["source"], configSchema: .object([:]),
            mascot: mascot, enabled: state == .enabled, config: .object([:]), lastRunAt: nil,
            state: state, error: nil)
    }

    // MARK: Stickers

    func testLooksFromManifests() {
        let qq = StickerLook(mascot: PluginMascot(sticker: "red_ribbon", accent: "#E8383F"))
        XCTAssertEqual(qq.sticker, "red_ribbon")
        XCTAssertEqual(qq.accent.hex, "#E8383F")
        // No sticker yet: keeps the plugin's color, shows the placeholder.
        let feishu = StickerLook(mascot: PluginMascot(sticker: nil, accent: "#22B8CF"))
        XCTAssertNil(feishu.sticker)
        XCTAssertEqual(feishu.accent.hex, "#22B8CF")
        // No mascot at all, or a broken accent: neutral gray.
        XCTAssertEqual(StickerLook(mascot: nil), StickerLook(sticker: nil))
        XCTAssertEqual(
            StickerLook(mascot: PluginMascot(sticker: "x", accent: "orange")).accent,
            MascotPalette.neutral)
        XCTAssertEqual(
            StickerLook(plugin: plugin(.enabled, mascot: PluginMascot(sticker: "a", accent: "#010203"))),
            StickerLook(sticker: "a", accent: MascotColor(hex: "#010203")!))
    }

    func testMissingStickersFallBackToThePlaceholder() {
        let catalog = StickerCatalog(names: ["lily_veil", "placeholder"])
        XCTAssertEqual(StickerLook(sticker: "lily_veil").resolved(in: catalog), "lily_veil")
        XCTAssertEqual(StickerLook(sticker: "not_drawn_yet").resolved(in: catalog), "placeholder")
        XCTAssertEqual(StickerLook(sticker: nil).resolved(in: catalog), "placeholder")
        XCTAssertEqual(StickerCatalog(names: ["b", "placeholder", "a"]).sorted, ["a", "b", "placeholder"])
    }

    func testCatalogReadsAFolder() throws {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("stickers-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for file in ["cat.png", "dog.PNG", "notes.txt"] {
            try Data([0]).write(to: folder.appendingPathComponent(file))
        }
        try Data(##"{"assistant": {"sticker": "cat", "accent": "#112233"}}"##.utf8)
            .write(to: folder.appendingPathComponent("app.json"))
        let catalog = StickerCatalog.load(folder: folder)
        XCTAssertEqual(catalog.names, ["cat", "dog"])
        XCTAssertEqual(catalog.assistant, StickerLook(sticker: "cat", accent: MascotColor(hex: "#112233")!))
        XCTAssertTrue(catalog.layered.isEmpty)
        // A folder with the three layers makes a sticker layered; the mask is optional.
        let layers = folder.appendingPathComponent("cat")
        try FileManager.default.createDirectory(at: layers, withIntermediateDirectories: true)
        for layer in StickerLayer.required.dropLast() {
            try Data([0]).write(to: layers.appendingPathComponent("\(layer.rawValue).png"))
        }
        XCTAssertFalse(StickerCatalog.load(folder: folder).hasLayers("cat"))
        try Data([0]).write(to: layers.appendingPathComponent("eyes_closed.png"))
        let layered = StickerCatalog.load(folder: folder)
        XCTAssertTrue(layered.hasLayers("cat"))
        XCTAssertFalse(layered.hasLayers("dog"))
        XCTAssertEqual(layered.names, ["cat", "dog"])  // the folder is not a sticker
        // No folder (e.g. previews): nothing bundled, everything shows the placeholder.
        let empty = StickerCatalog.load(folder: nil)
        XCTAssertTrue(empty.names.isEmpty)
        XCTAssertEqual(empty.assistant.resolved(in: empty), "placeholder")
    }

    func testTheRepositoryStickers() throws {
        // frontend/Design/stickers, the folder the App bundles.
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Design/stickers")
        let catalog = StickerCatalog.load(folder: folder)
        XCTAssertTrue(catalog.contains("placeholder"))
        for name in [
            "aemeath", "luosela", "moning", "shouanren", "lily_veil", "red_ribbon", "orange_daisy", "carlotta",
        ] {
            XCTAssertTrue(catalog.contains(name), name)
        }
        XCTAssertEqual(catalog.assistant.resolved(in: catalog), "aemeath")

        // Eye layers (scripts/sticker-layers.swift): all of them, on the sticker's canvas.
        XCTAssertEqual(
            catalog.layered,
            ["aemeath", "luosela", "moning", "shouanren", "lily_veil", "red_ribbon", "orange_daisy", "carlotta"])
        func size(_ url: URL) -> CGSize? {
            guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
            else { return nil }
            return CGSize(width: image.width, height: image.height)
        }
        for name in catalog.layered.sorted() {
            let sticker = size(folder.appendingPathComponent("\(name).png"))
            XCTAssertNotNil(sticker, name)
            for layer in StickerLayer.allCases {
                let url = folder.appendingPathComponent(name).appendingPathComponent("\(layer.rawValue).png")
                XCTAssertEqual(size(url), sticker, "\(name)/\(layer.rawValue)")
            }
        }
    }

    func testHexColors() {
        let color = MascotColor(hex: "#8068B2")
        XCTAssertEqual(color?.hex, "#8068B2")
        XCTAssertEqual(MascotColor(hex: "302840")?.red ?? 0, 48.0 / 255, accuracy: 1e-9)
        XCTAssertNil(MascotColor(hex: "#12345"))
        XCTAssertNil(MascotColor(hex: "#GG0000"))
        XCTAssertNil(MascotColor(hex: "+12345"))
    }

    func testColorsOnTheWatchsBlackScreen() throws {
        XCTAssertEqual(MascotColor(hex: "#000000")!.luminance, 0, accuracy: 1e-9)
        XCTAssertEqual(MascotColor(hex: "#FFFFFF")!.luminance, 1, accuracy: 1e-9)
        // 健康's purple and 作业's red are lifted toward white but keep their hue.
        for hex in ["#8068B2", "#D65F63", "#5B43C9"] {
            let color = try XCTUnwrap(MascotColor(hex: hex)), lifted = color.onBlack
            XCTAssertGreaterThanOrEqual(lifted.luminance, 0.3)
            XCTAssertLessThan(lifted.luminance, 0.4)
            XCTAssertGreaterThan(lifted.red, color.red)
            XCTAssertEqual(lifted.red > lifted.blue, color.red > color.blue)
        }
        // A light color stays as it is.
        let light = MascotColor(hex: "#FFC94A")!
        XCTAssertEqual(light.onBlack, light)
    }

    // MARK: Mapping API state to mascot state

    func testRunStates() {
        XCTAssertEqual(MascotState.forRun(nil, now: now), .idle)
        XCTAssertEqual(MascotState.forRun(run(.running), now: now), .working)
        XCTAssertEqual(MascotState.forRun(run(.failed, finishedAgo: 600), now: now), .error)
        XCTAssertEqual(MascotState.forRun(run(.cancelled, finishedAgo: 1), now: now), .error)
        XCTAssertEqual(MascotState.forRun(run(.succeeded, finishedAgo: 1), now: now), .done)
        XCTAssertEqual(MascotState.forRun(run(.succeeded, finishedAgo: 3), now: now), .idle)
        XCTAssertEqual(MascotState.forRun(run(.succeeded, finishedAgo: 600), now: now), .idle)
    }

    func testPluginStates() {
        XCTAssertEqual(MascotState.forPlugin(plugin(.disabled), runs: [], now: now), .offline)
        XCTAssertEqual(MascotState.forPlugin(plugin(.unsupported), runs: [], now: now), .offline)
        XCTAssertEqual(MascotState.forPlugin(plugin(.error), runs: [], now: now), .error)
        XCTAssertEqual(MascotState.forPlugin(plugin(.enabled), runs: [], now: now), .idle)
        let others = [run(.running, job: "fetch:qq_screen"), run(.failed, job: "extract_todos")]
        XCTAssertEqual(MascotState.forPlugin(plugin(.enabled), runs: others, now: now), .idle)
        let fetching = [run(.running, job: "fetch:feishu")]
        XCTAssertEqual(MascotState.forPlugin(plugin(.enabled), runs: fetching, now: now), .working)
        let job = [run(.running, job: "plugin:health:refresh")]
        XCTAssertEqual(
            MascotState.forPlugin(plugin(.enabled, id: "health"), runs: job, now: now), .working)
        // Newest first: the latest own run decides.
        let failedThenOK = [
            run(.succeeded, job: "fetch:feishu", finishedAgo: 1),
            run(.failed, job: "fetch:feishu", finishedAgo: 900),
        ]
        XCTAssertEqual(
            MascotState.forPlugin(plugin(.enabled), runs: failedThenOK, now: now), .done)
        XCTAssertEqual(
            MascotState.forPlugin(plugin(.enabled), runs: Array(failedThenOK.reversed()), now: now),
            .error)
    }

    func testKnowledgeState() {
        let goodnotes = plugin(.enabled, id: "goodnotes")
        let photos = plugin(.disabled, id: "class_photos")
        XCTAssertEqual(MascotState.forKnowledge(inputs: [goodnotes, photos], runs: [], now: now), .idle)
        // Organizing the knowledge base, or an input reading something, is work.
        let organizing = [run(.running, job: "knowledge_organize")]
        XCTAssertEqual(MascotState.forKnowledge(inputs: [], runs: organizing, now: now), .working)
        let scanning = [run(.running, job: "plugin:goodnotes:scan")]
        XCTAssertEqual(MascotState.forKnowledge(inputs: [goodnotes, photos], runs: scanning, now: now), .working)
        // A stopped input sleeps on its own; it does not put her to sleep.
        XCTAssertEqual(MascotState.forKnowledge(inputs: [photos], runs: [], now: now), .idle)
        XCTAssertEqual(MascotState.forKnowledge(inputs: [plugin(.error, id: "goodnotes")], runs: [], now: now), .error)
        // The night's run decides, not one Agent attempt inside it.
        var night = run(.failed, job: "knowledge_organize", finishedAgo: 600)
        night.harness = nil
        let attempt = run(.succeeded, job: "knowledge_organize", finishedAgo: 700)
        XCTAssertEqual(MascotState.forKnowledge(inputs: [], runs: [night, attempt], now: now), .error)
    }

    func testAssistantState() throws {
        let status = try JSONDecoder.assistant().decode(Status.self, from: Data(Fixtures.status.utf8))
        XCTAssertEqual(MascotState.forAssistant(status: nil, runs: [], now: now), .offline)
        var offline = status
        offline.online = false
        XCTAssertEqual(MascotState.forAssistant(status: offline, runs: [], now: now), .offline)
        // The fixture's last run failed with quota_exhausted.
        XCTAssertEqual(MascotState.forAssistant(status: status, runs: [], now: now), .error)
        XCTAssertEqual(
            MascotState.forAssistant(status: status, runs: [run(.running)], now: now), .working)
        XCTAssertEqual(
            MascotState.forAssistant(
                status: status, runs: [run(.succeeded, finishedAgo: 10)], now: now), .idle)
    }

    func testPriority() {
        XCTAssertEqual(MascotState.combined([]), .idle)
        XCTAssertEqual(MascotState.combined([.idle, .done]), .done)
        XCTAssertEqual(MascotState.combined([.done, .working, .idle]), .working)
        XCTAssertEqual(MascotState.combined([.working, .error]), .error)
        XCTAssertEqual(MascotState.combined([.error, .offline, .working]), .offline)
    }

    // MARK: State machine

    func testTransitions() {
        var machine = MascotStateMachine(state: .idle, since: now)
        machine.handle(.started, at: now)
        XCTAssertEqual(machine.state(at: now), .working)
        machine.handle(.succeeded, at: now.addingTimeInterval(5))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(5)), .done)
        XCTAssertEqual(machine.since, now.addingTimeInterval(5))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(7.9)), .done)
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(8)), .idle)  // done lasts 3 s

        machine.handle(.failed, at: now.addingTimeInterval(10))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(1000)), .error)  // stays
        machine.handle(.connected, at: now.addingTimeInterval(1001))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(1001)), .error)
        machine.handle(.started, at: now.addingTimeInterval(1002))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(1002)), .working)
    }

    func testOfflineWinsUntilReconnected() {
        var machine = MascotStateMachine(state: .working, since: now)
        machine.handle(.disconnected, at: now)
        for event in [MascotEvent.started, .succeeded, .failed] {
            machine.handle(event, at: now.addingTimeInterval(1))
            XCTAssertEqual(machine.state(at: now.addingTimeInterval(1)), .offline, "\(event)")
        }
        machine.handle(.connected, at: now.addingTimeInterval(2))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(2)), .idle)
        machine.handle(.disabled, at: now.addingTimeInterval(3))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(3)), .offline)
        machine.handle(.enabled, at: now.addingTimeInterval(4))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(4)), .idle)
    }

    func testRepeatedSuccessRestartsTheBounce() {
        var machine = MascotStateMachine(state: .idle, since: now, doneDuration: 3)
        machine.handle(.succeeded, at: now)
        machine.handle(.succeeded, at: now.addingTimeInterval(2))
        XCTAssertEqual(machine.since, now.addingTimeInterval(2))
        XCTAssertEqual(machine.state(at: now.addingTimeInterval(4.5)), .done)
        // Unchanged state keeps its start time.
        var idle = MascotStateMachine(state: .idle, since: now)
        idle.handle(.connected, at: now.addingTimeInterval(9))
        XCTAssertEqual(idle.since, now)
    }

    // MARK: Poses

    func testPosesDoNotMoveSideways() {
        // Only the motion layer (MascotMotion) leans the head towards something.
        for state in MascotState.allCases {
            for time in [0.0, 0.4, 1.3, 2.9] {
                XCTAssertEqual(MascotPose.at(time, state: state).offsetX, 0)
            }
        }
    }

    func testOverlaysAndColorPerState() {
        let expected: [MascotState: [MascotEffect]] = [
            .idle: [], .working: [], .done: [], .error: [.sweat], .offline: [.zzz],
        ]
        for (state, effects) in expected {
            for reduceMotion in [false, true] {
                let pose = MascotPose.at(12.3, state: state, since: 12, reduceMotion: reduceMotion)
                XCTAssertEqual(pose.effects, effects, "\(state)")
                XCTAssertEqual(pose.saturation, state == .offline ? 0.4 : 1, "\(state)")
            }
        }
    }

    func testReduceMotionIsStatic() {
        for state in MascotState.allCases {
            let first = MascotPose.at(0, state: state, reduceMotion: true)
            for time in [0.5, 1.7, 3.92, 100.0] {
                XCTAssertEqual(MascotPose.at(time, state: state, since: 0.2, reduceMotion: true), first)
            }
            XCTAssertEqual(first.offsetY, 0)
            XCTAssertEqual(first.scaleX, 1)
            XCTAssertEqual(first.scaleY, 1)
            XCTAssertEqual(first.rotation, MascotPose.restingRotation(for: state))
        }
        XCTAssertEqual(MascotPose.at(3, state: .error, reduceMotion: true).rotation, -8)
        XCTAssertEqual(MascotPose.at(3, state: .offline, reduceMotion: true).rotation, 4)
    }

    func testMotionPerState() {
        // Idle: floats and breathes, no tilt.
        let idle = (0..<60).map { MascotPose.at(Double($0) * 0.05, state: .idle) }
        XCTAssertTrue(idle.allSatisfy { abs($0.offsetY) <= 0.02 + 1e-9 && $0.rotation == 0 })
        XCTAssertGreaterThan(idle.map(\.offsetY).max()! - idle.map(\.offsetY).min()!, 0.03)
        XCTAssertTrue(idle.contains { $0.scaleX > 1.005 } && idle.contains { $0.scaleX < 0.995 })

        // Working: tilts the head to both sides.
        let working = (0..<24).map { MascotPose.at(Double($0) * 0.05, state: .working) }
        XCTAssertTrue(working.contains { $0.rotation > 3 } && working.contains { $0.rotation < -3 })

        // Done: jumps up right after the state starts, squashes on landing, then floats.
        let start = 100.0
        XCTAssertLessThan(MascotPose.at(start + 0.3, state: .done, since: start).offsetY, -0.07)
        XCTAssertEqual(MascotPose.at(start + 0.55, state: .done, since: start).scaleY, 0.94)
        XCTAssertLessThanOrEqual(abs(MascotPose.at(start + 2, state: .done, since: start).offsetY), 0.02)

        // Error: droops and tilts; the drop of sweat slides down every 2 s.
        let error = MascotPose.at(5, state: .error)
        XCTAssertGreaterThan(error.offsetY, 0)
        XCTAssertLessThan(error.rotation, -5)
        XCTAssertLessThan(error.scaleY, 1)
        XCTAssertEqual(MascotPose.at(5.5, state: .error).effectPhase, 0.75, accuracy: 1e-9)

        // Offline: tilted and breathing slowly while the zzz floats up.
        let asleep = (0..<40).map { MascotPose.at(Double($0) * 0.1, state: .offline) }
        XCTAssertTrue(asleep.allSatisfy { $0.offsetY == 0 && $0.rotation == 4 })
        XCTAssertTrue(asleep.contains { $0.scaleX > 1.01 } && asleep.contains { $0.scaleX < 0.99 })
        XCTAssertTrue(asleep.allSatisfy { (0..<1).contains($0.effectPhase) })
    }
}
