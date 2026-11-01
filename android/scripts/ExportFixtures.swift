import Foundation
@main struct Export {
  static func main() async throws {
    let root = URL(fileURLWithPath: CommandLine.arguments[1])
    let encoder = JSONEncoder.assistant()
    func write<T: Encodable>(_ value: T, _ path: String) throws {
      try encoder.encode(value).write(to: root.appendingPathComponent(path))
    }
    let now = APIDateCoding.date(from: "2026-10-31T10:00:00+08:00")!
    let mock = MockBackend(now: now, calendar: DueText.calendar(timeZone: TimeZone(identifier: "Asia/Shanghai")!), clock: { now })
    try await write(mock.status(), "main/resources/mock/status.json")
    try await write(mock.today(), "main/resources/mock/today.json")
    try await write(mock.todos(status: nil), "main/resources/mock/todos.json")
    try await write(mock.digests(), "main/resources/mock/digests.json")
    try await write(mock.runs(), "main/resources/mock/runs.json")
    let plugins = try await mock.plugins()
    try write(plugins, "main/resources/mock/plugins.json")
    for p in plugins { try await write(mock.cards(pluginID:p.id), "main/resources/mock/cards-\(p.id).json") }
    try await write(mock.settings(), "main/resources/mock/settings.json")
    try await write(mock.healthReports(), "main/resources/mock/reports.json")
    let library = try await mock.knowledge()
    try write(library, "main/resources/mock/knowledge.json")
    var files: [String: KnowledgeFiles] = [:]
    var contents: [String: KnowledgeFile] = [:]
    for area in library.areas {
      let listing = try await mock.knowledgeFiles(path: area.path)
      files[area.path] = listing
      for entry in listing.entries { contents[entry.path] = try await mock.knowledgeFile(path:entry.path) }
    }
    try write(files, "main/resources/mock/knowledge-files.json")
    try write(contents, "main/resources/mock/knowledge-contents.json")
    let fixtures = ["todo":Fixtures.todo,"todoWithoutDue":Fixtures.todoWithoutDue,"todoWithSources":Fixtures.todoWithSources,"todoPage":Fixtures.todoPage,"status":Fixtures.status,"today":Fixtures.today,"digests":Fixtures.digests,"runs":Fixtures.runs,"settings":Fixtures.settings,"plugins":Fixtures.plugins,"plugin":Fixtures.plugin,"healthReports":Fixtures.healthReports,"healthCards":Fixtures.healthCards,"jobAccepted":Fixtures.jobAccepted,"healthAccepted":Fixtures.healthAccepted]
    for (name, json) in fixtures { try Data(json.utf8).write(to:root.appendingPathComponent("test/resources/fixtures/\(name).json")) }
  }
}
