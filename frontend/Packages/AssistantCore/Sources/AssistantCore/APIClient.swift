import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

public enum APIError: Error, Equatable, Sendable {
    /// No token stored on this device yet.
    case missingToken
    case unauthorized
    /// The token is valid but lacks the scope for this route (403), e.g. an MCP-only token.
    case forbidden
    case notFound
    /// The request conflicts with the current state (409), e.g. a job already running.
    case conflict
    case invalidRequest
    case server(status: Int)
    case invalidResponse
    case decoding(String)
    case transport(String)
}

/// Talks to the backend over HTTP; the Bearer token comes from a `TokenProvider`
/// (the Keychain in the Apps). The App never holds any business logic beyond this.
public struct APIClient: Sendable {
    public let baseURL: URL
    let tokens: any TokenProvider
    let session: URLSession

    public init(baseURL: URL, tokens: any TokenProvider, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.session = session
    }

    // MARK: Endpoints of docs/api.md

    public func status() async throws -> Status {
        try await get("status")
    }

    public func today() async throws -> Today {
        try await get("today")
    }

    public func todos(status: TodoStatus? = nil, limit: Int = 50, offset: Int = 0)
        async throws -> Page<Todo>
    {
        var query = paging(limit, offset)
        if let status { query.append(URLQueryItem(name: "status", value: status.rawValue)) }
        return try await get("todos", query: query)
    }

    public func updateTodo(id: String, _ patch: TodoPatch) async throws -> Todo {
        try await send("PATCH", ["todos", id], body: patch)
    }

    /// `date` is a calendar day, `YYYY-MM-DD`.
    public func digests(date: String? = nil, plugin: String? = nil, limit: Int = 50, offset: Int = 0)
        async throws -> Page<Digest>
    {
        var query = paging(limit, offset)
        if let date { query.append(URLQueryItem(name: "date", value: date)) }
        if let plugin { query.append(URLQueryItem(name: "plugin", value: plugin)) }
        return try await get("digests", query: query)
    }

    public func runs(limit: Int = 50, offset: Int = 0) async throws -> Page<Run> {
        try await get("runs", query: paging(limit, offset))
    }

    /// 202 means the job started, not that it succeeded; poll `runs()`.
    public func runJob(_ jobID: String) async throws -> JobAccepted {
        try await send("POST", ["jobs", jobID, "run"], body: Optional<String>.none)
    }

    public func plugins() async throws -> [PluginView] {
        try await get("plugins")
    }

    public func updatePlugin(id: String, _ patch: PluginPatch) async throws -> PluginView {
        try await send("PATCH", ["plugins", id], body: patch)
    }

    public func deletePlugin(id: String) async throws {
        _ = try await self.data("DELETE", ["plugins", id], body: nil)
    }

    public func cards(pluginID: String) async throws -> [Card] {
        try await send("GET", ["plugins", pluginID, "cards"], body: Optional<String>.none)
    }

    public func uploadHealth(_ batch: HealthBatch) async throws -> HealthAccepted {
        try await send("POST", ["health", "samples"], body: batch)
    }

    /// `date` is a calendar day, `YYYY-MM-DD`.
    public func healthReports(
        date: String? = nil, kind: HealthReportKind? = nil, status: HealthReportStatus? = nil,
        limit: Int = 50, offset: Int = 0
    ) async throws -> Page<HealthReport> {
        var query = paging(limit, offset)
        if let date { query.append(URLQueryItem(name: "date", value: date)) }
        if let kind { query.append(URLQueryItem(name: "kind", value: kind.rawValue)) }
        if let status { query.append(URLQueryItem(name: "status", value: status.rawValue)) }
        let payload = try await self.data("GET", ["health", "reports"], query: query, body: nil)
        return try decode(payload)
    }

    public func settings() async throws -> RuntimeSettings {
        try await get("settings")
    }

    public func updateSettings(_ patch: SettingsPatch) async throws -> RuntimeSettings {
        try await send("PATCH", ["settings"], body: patch)
    }

    /// Newest first.
    public func chatMessages(botID: String, limit: Int = 50, offset: Int = 0)
        async throws -> Page<ChatMessage>
    {
        let payload = try await self.data(
            "GET", ["bots", botID, "messages"], query: paging(limit, offset), body: nil)
        return try decode(payload)
    }

    /// 202 means the reply is pending; poll `chatMessages` until it is not.
    public func sendChat(botID: String, text: String, botName: String?, choice: ChatChoice = .automatic)
        async throws -> ChatAccepted
    {
        try await send(
            "POST", ["bots", botID, "messages"], body: ChatSend(text: text, botName: botName, choice: choice))
    }

    // MARK: Knowledge base (docs/knowledge.md)

    public func knowledge() async throws -> KnowledgeOverview {
        try await get("knowledge")
    }

    public func updateKnowledge(_ patch: KnowledgePatch) async throws -> KnowledgeOverview {
        try await send("PATCH", ["knowledge"], body: patch)
    }

    public func setReaders(_ readers: [String], area path: String) async throws -> KnowledgeArea {
        try await send("PATCH", ["knowledge", "access"], body: KnowledgeAccess(path: path, readers: readers))
    }

    public func createTopic(name: String, readers: [String]?) async throws -> KnowledgeArea {
        try await send("POST", ["knowledge", "topics"], body: KnowledgeTopic(name: name, readers: readers))
    }

    public func knowledgeFiles(path: String) async throws -> KnowledgeFiles {
        let payload = try await data("GET", ["knowledge", "files"], query: [URLQueryItem(name: "path", value: path)], body: nil)
        return try decode(payload)
    }

    public func knowledgeFile(path: String) async throws -> KnowledgeFile {
        let payload = try await data("GET", ["knowledge", "file"], query: [URLQueryItem(name: "path", value: path)], body: nil)
        return try decode(payload)
    }

    public func knowledgeOriginal(path: String) async throws -> Data {
        try await data(
            "GET", ["knowledge", "raw"], query: [URLQueryItem(name: "path", value: path)], body: nil, accept: "*/*")
    }

    public func searchKnowledge(_ query: String, limit: Int = 30) async throws -> [KnowledgeSearchResult] {
        let payload = try await data(
            "GET", ["knowledge", "search"],
            query: [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: String(limit))],
            body: nil)
        let page: KnowledgeSearchPage = try decode(payload)
        return page.results
    }

    public func uploadKnowledge(path: String, data body: Data, contentType: String) async throws -> KnowledgeEntry {
        let payload = try await data(
            "POST", ["knowledge", "files"], query: [URLQueryItem(name: "path", value: path)], body: body,
            contentType: contentType)
        return try decode(payload)
    }

    // MARK: Plumbing

    func paging(_ limit: Int, _ offset: Int) -> [URLQueryItem] {
        [URLQueryItem(name: "limit", value: String(limit)),
         URLQueryItem(name: "offset", value: String(offset))]
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        let payload = try await self.data("GET", [path], query: query, body: nil)
        return try decode(payload)
    }

    func send<T: Decodable, Body: Encodable>(_ method: String, _ path: [String], body: Body?)
        async throws -> T
    {
        let encoded = try body.map { try JSONEncoder.assistant().encode($0) }
        let payload = try await self.data(method, path, body: encoded)
        return try decode(payload)
    }

    func decode<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try JSONDecoder.assistant().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }

    /// Path characters allowed inside one segment (a `/` in an ID must not split it).
    var segmentAllowed: CharacterSet {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        return allowed
    }

    func url(_ path: [String], query: [URLQueryItem]) throws -> URL {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidRequest
        }
        let base = components.percentEncodedPath.hasSuffix("/")
            ? String(components.percentEncodedPath.dropLast()) : components.percentEncodedPath
        let segments = (["api", "v1"] + path).map {
            $0.addingPercentEncoding(withAllowedCharacters: segmentAllowed) ?? $0
        }
        components.percentEncodedPath = base + "/" + segments.joined(separator: "/")
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw APIError.invalidRequest }
        return url
    }

    func data(
        _ method: String, _ path: [String], query: [URLQueryItem] = [], body: Data?,
        contentType: String = "application/json", accept: String = "application/json"
    ) async throws -> Data {
        guard let token = try tokens.currentToken(), !token.isEmpty else {
            throw APIError.missingToken
        }
        var request = URLRequest(url: try url(path, query: query))
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(accept, forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: request)
        } catch {
            throw APIError.transport(String(describing: error))
        }
        let (payload, response) = result
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        switch http.statusCode {
        case 200..<300: return payload
        case 401: throw APIError.unauthorized
        case 403: throw APIError.forbidden
        case 404: throw APIError.notFound
        case 409: throw APIError.conflict
        case 422: throw APIError.invalidRequest
        default: throw APIError.server(status: http.statusCode)
        }
    }
}
