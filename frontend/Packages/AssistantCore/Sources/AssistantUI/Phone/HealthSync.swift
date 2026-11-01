#if os(iOS) && canImport(HealthKit)
    import AssistantCore
    import Foundation
    import HealthKit
    import UIKit

    /// Reads the user's health samples on the iPhone and uploads them to their own Mac
    /// (POST /api/v1/health/samples, docs/api.md 「健康样本约定」). The watch's data reaches
    /// HealthKit on the iPhone, so the watch needs nothing of its own.
    ///
    /// Each type is read with an anchored query, so a sync only sends what is new since the last
    /// one; the anchor is kept only once the backend has taken the samples. The first sync goes
    /// back `firstDays`. It runs when the App starts or comes to the front (at most every few
    /// minutes), from 设置 → 健康数据, and when HealthKit wakes the App for new samples
    /// (background delivery, hourly at most). Nothing is sent while the App shows mock data.
    @MainActor
    public final class HealthSync: ObservableObject {
        public static let shared = HealthSync()

        @Published public private(set) var lastSync: Date?
        @Published public private(set) var lastCount = 0
        @Published public private(set) var syncing = false
        /// The last failure in words, until a sync succeeds.
        @Published public private(set) var problem: String?
        /// Whether the App asked for access on this device (HealthKit never says what was
        /// granted for reading; types the user turned off just return nothing).
        @Published public private(set) var asked: Bool

        static let firstDays = 14
        static let minimumGap: TimeInterval = 5 * 60
        static let batchSize = 1000
        static let pageSize = 5000

        private let health = HKHealthStore()
        private weak var store: AppStore?
        private var observing = false
        private let defaults = UserDefaults.standard

        init() {
            asked = defaults.bool(forKey: "health.asked")
            let stamp = defaults.double(forKey: "health.lastSync")
            lastSync = stamp > 0 ? Date(timeIntervalSince1970: stamp) : nil
            lastCount = defaults.integer(forKey: "health.lastCount")
        }

        public var available: Bool {
            UIDevice.current.userInterfaceIdiom != .pad && HKHealthStore.isHealthDataAvailable()
        }

        /// Call once at launch: watches for new samples and syncs when it can.
        public func attach(_ store: AppStore) {
            guard available else { return }
            self.store = store
            if asked { observe() }
            Task {
                // Wait for the first refresh, so a real backend is known before asking.
                try? await Task.sleep(for: .seconds(3))
                if !self.asked, !store.connection.useMockData { await self.requestAccess() }
                await self.sync(force: false)
            }
        }

        /// The system sheet that asks which types the App may read.
        public func requestAccess() async {
            guard available else {
                problem = "这台设备没有「健康」数据"
                return
            }
            do {
                let types = Set(Self.kinds.map { $0.type as HKObjectType })
                try await health.requestAuthorization(toShare: [], read: types)
                asked = true
                defaults.set(true, forKey: "health.asked")
                observe()
            } catch {
                problem = "没能打开健康数据授权：\(error.localizedDescription)"
            }
        }

        /// Uploads what is new. `force` ignores the few-minutes gap (设置's button).
        public func sync(force: Bool) async {
            guard available, asked, !syncing, let store, !store.connection.useMockData else { return }
            if !force, let lastSync, Date().timeIntervalSince(lastSync) < Self.minimumGap { return }
            syncing = true
            defer { syncing = false }
            var sent = 0
            do {
                for kind in Self.kinds {
                    sent += try await sync(kind, to: store.backend)
                }
                problem = nil
                lastSync = Date()
                lastCount = sent
                defaults.set(lastSync?.timeIntervalSince1970, forKey: "health.lastSync")
                defaults.set(sent, forKey: "health.lastCount")
            } catch let error as APIError {
                problem = "上传失败：\(error.localizedDescription)"
            } catch {
                problem = "读取健康数据失败：\(error.localizedDescription)"
            }
        }

        // MARK: One type

        private func sync(_ kind: Kind, to backend: any Backend) async throws -> Int {
            var anchor = loadAnchor(kind)
            let start = Calendar.current.date(byAdding: .day, value: -Self.firstDays, to: Date())
            let predicate = anchor == nil ? HKQuery.predicateForSamples(withStart: start, end: nil) : nil
            var sent = 0
            while true {
                let query = HKAnchoredObjectQueryDescriptor(
                    predicates: [.sample(type: kind.type, predicate: predicate)], anchor: anchor,
                    limit: Self.pageSize)
                let result = try await query.result(for: health)
                let samples = result.addedSamples.compactMap(kind.sample)
                for start in stride(from: 0, to: samples.count, by: Self.batchSize) {
                    let batch = Array(samples[start..<min(start + Self.batchSize, samples.count)])
                    _ = try await backend.uploadHealth(HealthBatch(samples: batch))
                }
                sent += samples.count
                anchor = result.newAnchor
                saveAnchor(result.newAnchor, kind)
                if result.addedSamples.count < Self.pageSize { return sent }
            }
        }

        private func anchorKey(_ kind: Kind) -> String { "health.anchor.\(kind.type.identifier)" }

        private func loadAnchor(_ kind: Kind) -> HKQueryAnchor? {
            guard let data = defaults.data(forKey: anchorKey(kind)) else { return nil }
            return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
        }

        private func saveAnchor(_ anchor: HKQueryAnchor, _ kind: Kind) {
            if let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) {
                defaults.set(data, forKey: anchorKey(kind))
            }
        }

        // MARK: Background delivery

        /// HealthKit wakes the App (at most hourly) when a type gets new samples.
        private func observe() {
            guard available, !observing else { return }
            observing = true
            for kind in Self.kinds {
                let query = HKObserverQuery(sampleType: kind.type, predicate: nil) { _, done, error in
                    guard error == nil else {
                        done()
                        return
                    }
                    Task { @MainActor in
                        await HealthSync.shared.sync(force: true)
                        done()
                    }
                }
                health.execute(query)
                health.enableBackgroundDelivery(for: kind.type, frequency: .hourly) { _, _ in }
            }
        }

        // MARK: What is read

        /// A HealthKit type and how its samples become the backend's HealthSample.
        struct Kind {
            let type: HKSampleType
            let sample: (HKSample) -> HealthSample?
        }

        static func quantity(
            _ identifier: HKQuantityTypeIdentifier, as name: String, _ unit: HKUnit, _ unitText: String
        ) -> Kind {
            Kind(type: HKQuantityType(identifier)) { sample in
                guard let sample = sample as? HKQuantitySample else { return nil }
                return HealthSample(
                    id: sample.uuid.uuidString, type: name, value: sample.quantity.doubleValue(for: unit),
                    unit: unitText, startAt: sample.startDate, endAt: sample.endDate,
                    source: sample.sourceRevision.source.name)
            }
        }

        static let perMinute = HKUnit.count().unitDivided(by: .minute())

        static var kinds: [Kind] {
            var kinds = [
                quantity(.heartRate, as: "heart_rate", perMinute, "count/min"),
                quantity(.restingHeartRate, as: "resting_heart_rate", perMinute, "count/min"),
                quantity(.heartRateVariabilitySDNN, as: "hrv_sdnn", .secondUnit(with: .milli), "ms"),
                quantity(.respiratoryRate, as: "respiratory_rate", perMinute, "count/min"),
                quantity(.oxygenSaturation, as: "oxygen_saturation", .percent(), "%"),
                quantity(.appleSleepingWristTemperature, as: "wrist_temperature", .degreeCelsius(), "degC"),
                quantity(.stepCount, as: "step_count", .count(), "count"),
                quantity(.activeEnergyBurned, as: "active_energy", .kilocalorie(), "kcal"),
                quantity(.vo2Max, as: "vo2max", HKUnit(from: "ml/kg*min"), "mL/min·kg"),
                Kind(type: HKCategoryType(.sleepAnalysis), sample: sleep),
                Kind(type: HKWorkoutType.workoutType(), sample: workout),
            ]
            // Only in the iOS 27 SDK (Xcode 27, Swift 6.4); older Xcodes, like CI's, build without it.
            #if compiler(>=6.4)
                if #available(iOS 27.0, *) {
                    kinds.append(quantity(.heartRateVariabilityRMSSD, as: "hrv_rmssd", .secondUnit(with: .milli), "ms"))
                }
            #endif
            return kinds
        }

        /// A sleep stage, as the backend's aliases (one sample a stage).
        static func sleep(_ sample: HKSample) -> HealthSample? {
            guard let sample = sample as? HKCategorySample,
                let value = HKCategoryValueSleepAnalysis(rawValue: sample.value)
            else { return nil }
            let stage: String
            switch value {
            case .inBed: stage = "sleep_in_bed"
            case .asleepUnspecified: stage = "sleep_asleep"
            case .awake: stage = "sleep_awake"
            case .asleepCore: stage = "sleep_core"
            case .asleepDeep: stage = "sleep_deep"
            case .asleepREM: stage = "sleep_rem"
            @unknown default: return nil
            }
            return HealthSample(
                id: sample.uuid.uuidString, type: stage, value: 1, unit: "", startAt: sample.startDate,
                endAt: sample.endDate, source: sample.sourceRevision.source.name)
        }

        /// A workout as `workout:<activity>` with its length in minutes.
        static func workout(_ sample: HKSample) -> HealthSample? {
            guard let workout = sample as? HKWorkout else { return nil }
            return HealthSample(
                id: workout.uuid.uuidString, type: "workout:\(activity(workout.workoutActivityType))",
                value: workout.duration / 60, unit: "min", startAt: workout.startDate, endAt: workout.endDate,
                source: workout.sourceRevision.source.name)
        }

        static func activity(_ type: HKWorkoutActivityType) -> String {
            switch type {
            case .running: return "Running"
            case .walking: return "Walking"
            case .cycling: return "Cycling"
            case .swimming: return "Swimming"
            case .hiking: return "Hiking"
            case .traditionalStrengthTraining: return "TraditionalStrengthTraining"
            case .functionalStrengthTraining: return "FunctionalStrengthTraining"
            case .highIntensityIntervalTraining: return "HighIntensityIntervalTraining"
            case .coreTraining: return "CoreTraining"
            case .yoga: return "Yoga"
            case .pilates: return "Pilates"
            case .elliptical: return "Elliptical"
            case .rowing: return "Rowing"
            case .stairClimbing: return "StairClimbing"
            case .jumpRope: return "JumpRope"
            case .mixedCardio: return "MixedCardio"
            case .basketball: return "Basketball"
            case .soccer: return "Soccer"
            case .badminton: return "Badminton"
            case .tableTennis: return "TableTennis"
            case .tennis: return "Tennis"
            case .volleyball: return "Volleyball"
            case .socialDance, .cardioDance: return "Dance"
            case .mindAndBody: return "MindAndBody"
            default: return "Other"
            }
        }
    }
#endif
