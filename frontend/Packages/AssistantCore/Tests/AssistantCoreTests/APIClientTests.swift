import XCTest

@testable import AssistantCore

/// Answers requests from a closure so the client is tested without a network.
final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) -> (Int, String))?
    static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        StubProtocol.requests.append(request)
        let (status, body) = StubProtocol.handler?(request) ?? (500, "")
        let response = HTTPURLResponse(
            url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    static func body(of request: URLRequest) -> String? {
        if let data = request.httpBody { return String(decoding: data, as: UTF8.self) }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return String(decoding: data, as: UTF8.self)
    }
}

final class APIClientTests: XCTestCase {
    var session: URLSession!

    override func setUp() {
        super.setUp()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        session = URLSession(configuration: configuration)
        StubProtocol.requests = []
        StubProtocol.handler = nil
    }

    func client(_ base: String = "https://mini.example.ts.net", token: String? = "secret-token")
        -> APIClient
    {
        APIClient(baseURL: URL(string: base)!, tokens: StaticTokenProvider(token), session: session)
    }

    func testBearerTokenPathAndDecoding() async throws {
        StubProtocol.handler = { _ in (200, Fixtures.todoPage) }
        let page = try await client().todos(status: .pending, limit: 20, offset: 40)
        XCTAssertEqual(page.total, 2)
        let request = try XCTUnwrap(StubProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret-token")
        let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        XCTAssertEqual(components.path, "/api/v1/todos")
        XCTAssertEqual(
            Set(components.queryItems ?? []),
            [URLQueryItem(name: "limit", value: "20"), URLQueryItem(name: "offset", value: "40"),
             URLQueryItem(name: "status", value: "pending")])
    }

    func testEveryEndpointHitsTheDocumentedRoute() async throws {
        StubProtocol.handler = { request in
            switch (request.httpMethod ?? "", request.url!.path) {
            case ("GET", "/prefix/api/v1/status"): return (200, Fixtures.status)
            case ("GET", "/prefix/api/v1/today"): return (200, Fixtures.today)
            case ("PATCH", "/prefix/api/v1/todos/ab0904cc"): return (200, Fixtures.todo)
            case ("GET", "/prefix/api/v1/digests"): return (200, Fixtures.digests)
            case ("GET", "/prefix/api/v1/runs"): return (200, Fixtures.runs)
            case ("POST", "/prefix/api/v1/jobs/fetch:feishu/run"): return (202, Fixtures.jobAccepted)
            case ("GET", "/prefix/api/v1/plugins"): return (200, Fixtures.plugins)
            case ("PATCH", "/prefix/api/v1/plugins/feishu"): return (200, Fixtures.plugin)
            case ("DELETE", "/prefix/api/v1/plugins/feishu"): return (204, "")
            case ("GET", "/prefix/api/v1/plugins/health/cards"): return (200, "[]")
            case ("POST", "/prefix/api/v1/health/samples"): return (200, Fixtures.healthAccepted)
            case ("GET", "/prefix/api/v1/health/reports"): return (200, Fixtures.healthReports)
            case ("GET", "/prefix/api/v1/settings"): return (200, Fixtures.settings)
            case ("PATCH", "/prefix/api/v1/settings"): return (200, Fixtures.settings)
            default: return (404, #"{"detail": "not_found"}"#)
            }
        }
        // A base URL with a path (e.g. behind a reverse proxy) keeps its prefix.
        let api = client("https://mini.example.ts.net/prefix/")
        _ = try await api.status()
        let today = try await api.today()
        XCTAssertEqual(today.todos.count, 1)
        _ = try await api.updateTodo(id: "ab0904cc", .complete())
        XCTAssertEqual(StubProtocol.body(of: StubProtocol.requests.last!), #"{"status":"completed"}"#)
        XCTAssertEqual(
            StubProtocol.requests.last?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        _ = try await api.digests(date: "2026-10-15", plugin: "qq_screen")
        XCTAssertTrue(StubProtocol.requests.last!.url!.query!.contains("date=2026-10-15"))
        _ = try await api.runs()
        let job = try await api.runJob("fetch:feishu")
        XCTAssertEqual(job.status, "accepted")
        let plugins = try await api.plugins()
        XCTAssertEqual(plugins.count, 1)
        let enabled = try await api.updatePlugin(id: "feishu", PluginPatch(enabled: true))
        XCTAssertEqual(enabled.id, "feishu")
        XCTAssertEqual(StubProtocol.body(of: StubProtocol.requests.last!), #"{"enabled":true}"#)
        try await api.deletePlugin(id: "feishu")
        let cards = try await api.cards(pluginID: "health")
        XCTAssertEqual(cards, [])
        let sample = HealthSample(
            id: "s", type: "step_count", value: 1, unit: "count", startAt: Date(),
            endAt: Date(), source: "iPhone")
        let accepted = try await api.uploadHealth(HealthBatch(samples: [sample]))
        XCTAssertEqual(accepted.accepted, 1)
        let reports = try await api.healthReports(kind: .morning, status: .ready)
        XCTAssertEqual(reports.items.count, 2)
        let query = StubProtocol.requests.last!.url!.query!
        XCTAssertTrue(query.contains("kind=morning") && query.contains("status=ready"), query)
        let settings = try await api.settings()
        XCTAssertEqual(settings.timezone, "Asia/Shanghai")
        var patch = SettingsPatch()
        patch.timezone = "Asia/Shanghai"
        _ = try await api.updateSettings(patch)
        XCTAssertEqual(StubProtocol.requests.count, 14)
        XCTAssertTrue(
            StubProtocol.requests.allSatisfy {
                $0.value(forHTTPHeaderField: "Authorization") == "Bearer secret-token"
            })
    }

    func testErrorsMapToCases() async {
        let cases: [(Int, APIError)] = [
            (401, .unauthorized), (403, .forbidden), (404, .notFound), (409, .conflict),
            (422, .invalidRequest), (500, .server(status: 500)),
        ]
        for (status, expected) in cases {
            StubProtocol.handler = { _ in (status, #"{"detail": "x"}"#) }
            do {
                _ = try await client().status()
                XCTFail("expected \(expected)")
            } catch {
                XCTAssertEqual(error as? APIError, expected)
            }
        }
        StubProtocol.handler = { _ in (200, #"{"online": "yes"}"#) }
        do {
            _ = try await client().status()
            XCTFail("expected a decoding error")
        } catch let APIError.decoding(message) {
            XCTAssertFalse(message.isEmpty)
        } catch {
            XCTFail("\(error)")
        }
    }

    func testMissingTokenNeverSendsARequest() async {
        StubProtocol.handler = { _ in (200, Fixtures.status) }
        for token in [nil, ""] as [String?] {
            do {
                _ = try await client(token: token).status()
                XCTFail("expected missingToken")
            } catch {
                XCTAssertEqual(error as? APIError, .missingToken)
            }
        }
        XCTAssertTrue(StubProtocol.requests.isEmpty)
    }

    func testPathSegmentsAreEscaped() throws {
        let url = try client().url(["todos", "a/b c"], query: [])
        XCTAssertEqual(url.absoluteString, "https://mini.example.ts.net/api/v1/todos/a%2Fb%20c")
    }
}
