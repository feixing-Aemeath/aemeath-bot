import XCTest

@testable import AssistantCore

final class KnowledgeTests: XCTestCase {
    static let overview = """
        {
          "readers": [
            {"id": "助理", "kind": "bot", "sticker": "aemeath"},
            {"id": "作业", "kind": "bot", "sticker": "moning"},
            {"id": "Claude Code", "kind": "agent", "sticker": null}
          ],
          "default_readers": ["助理"],
          "areas": [
            {"path": "知识库/课程", "name": "课程", "kind": "topic", "readers": ["助理", "Claude Code"],
             "locked": [], "notes": 12, "sources": 5, "files": 17, "pending": 1, "failed": 0,
             "updated_at": "2026-10-29T03:10:00+08:00"},
            {"path": "Bots", "name": "Bots", "kind": "system", "readers": ["助理", "作业"],
             "locked": ["助理", "作业"], "notes": 0, "sources": 0, "files": 13, "pending": 0, "failed": 0,
             "updated_at": null}
          ],
          "loose_files": 2,
          "organize": {"last_run_at": "2026-10-29T03:00:05.123456+08:00", "status": "succeeded", "next_run_at": null}
        }
        """

    static let note = """
        {
          "path": "知识库/课程/整理/最短路径.md", "name": "最短路径.md", "title": "最短路径：Dijkstra",
          "kind": "note", "format": "md", "size": 2048, "modified_at": "2026-10-29T03:10:00+08:00",
          "area": "知识库/课程", "readers": ["助理"],
          "content": "要点。\\n\\n## 来源\\n\\n- [[知识库/课程/第5章 图.pdf]] 第 18 页\\n\\n## 我的批注\\n\\n看 [[图的遍历|遍历]]。",
          "extracted": false, "truncated": false,
          "sources": [{"path": "知识库/课程/第5章 图.pdf", "label": "第 18 页"}],
          "notes": [], "organized_by": "codex", "organized_at": "2026-10-29T03:10:00+08:00",
          "status": null, "error": null
        }
        """

    func decode<T: Decodable>(_ type: T.Type, _ text: String) throws -> T {
        try JSONDecoder.assistant().decode(T.self, from: Data(text.utf8))
    }

    func testOverviewDecodesTheDocumentedShape() throws {
        let overview = try decode(KnowledgeOverview.self, Self.overview)
        XCTAssertEqual(overview.topics.map(\.name), ["课程"])
        XCTAssertEqual(overview.vaultFolders.map(\.path), ["Bots"])
        XCTAssertEqual(overview.bots.map(\.id), ["助理", "作业"])
        XCTAssertEqual(overview.agents.map(\.id), ["Claude Code"])
        XCTAssertEqual(overview.looseFiles, 2)
        XCTAssertEqual(overview.pending, 1)
        XCTAssertNotNil(overview.organize.lastRunAt)
        XCTAssertNil(overview.organize.nextRunAt)
        XCTAssertEqual(overview.topics[0].summary, "12 篇整理 · 5 份资料")
        XCTAssertEqual(overview.vaultFolders[0].summary, "13 个文件")
        // Readers come back in the backend's order, whatever order they were chosen in.
        XCTAssertEqual(overview.ordered(["Claude Code", "助理"]).map(\.id), ["助理", "Claude Code"])
    }

    func testAnOlderAreaWithoutOptionalFieldsStillDecodes() throws {
        let area = try decode(KnowledgeArea.self, #"{"path": "知识库/x", "name": "x", "readers": []}"#)
        XCTAssertTrue(area.isTopic)
        XCTAssertEqual(area.files, 0)
        XCTAssertEqual(area.summary, "空的，放点资料进来")
    }

    func testANoteShowsWithoutItsSourcesSectionAndWithReadableLinks() throws {
        let file = try decode(KnowledgeFile.self, Self.note)
        XCTAssertEqual(file.sources.first?.name, "第5章 图.pdf")
        XCTAssertEqual(file.displayText, "要点。\n\n## 我的批注\n\n看 遍历。")
        XCTAssertFalse(file.hasOriginal)
    }

    func testLinksReadAsObsidianShowsThem() {
        XCTAssertEqual(Knowledge.readable("见 [[知识库/课程/第5章 图.pdf]]。"), "见 第5章 图.pdf。")
        XCTAssertEqual(Knowledge.readable("![[a/b.png]] 和 [[c|别名]]"), "b.png 和 别名")
        XCTAssertEqual(Knowledge.readable("没有链接"), "没有链接")
    }

    func testDroppingASectionKeepsTheRest() {
        let text = "# 标题\n正文\n## 来源\n- a\n### 细节\n- b\n## 我的批注\n批注"
        XCTAssertEqual(Knowledge.dropSection("来源", from: text), "# 标题\n正文\n## 我的批注\n批注")
        XCTAssertEqual(Knowledge.dropSection("没有", from: text), text)
    }

    func testTopicNamesAndUploadTypes() {
        XCTAssertTrue(Knowledge.validTopic("数据结构"))
        for bad in ["", ".隐藏", "a/b", "a:b", "整理", String(repeating: "长", count: 41)] {
            XCTAssertFalse(Knowledge.validTopic(bad), bad)
        }
        XCTAssertEqual(Knowledge.contentType(for: "第5章.PDF"), "application/pdf")
        XCTAssertEqual(Knowledge.contentType(for: "板书.heic"), "image/heic")
        XCTAssertNil(Knowledge.contentType(for: "表格.xlsx"))
    }

    func testEntryStatusReadsInChinese() {
        let entry = KnowledgeEntry(
            path: "知识库/x/a.pdf", name: "a.pdf", title: "a", kind: "source", format: "pdf", status: "skipped")
        XCTAssertEqual(entry.statusText, "不整理：这个主题谁都不能读")
        XCTAssertEqual(entry.symbol, "doc.richtext")
    }

    func testMockKeepsLockedReadersAndRefusesWhatTheAPIRefuses() async throws {
        let mock = MockBackend()
        let overview = try await mock.knowledge()
        XCTAssertEqual(overview.readers.count, 9)  // seven Bots, two Agents
        XCTAssertTrue(overview.topics.contains { $0.readers.isEmpty })

        let bots = try await mock.setReaders(["Codex"], area: "Bots")
        XCTAssertEqual(bots.readers.count, 8, "every Bot stays, Codex joins")
        do {
            _ = try await mock.setReaders(["谁"], area: "知识库/训练")
            XCTFail("an unknown reader is refused")
        } catch APIError.invalidRequest {}

        let topic = try await mock.createTopic(name: "英语", readers: nil)
        XCTAssertEqual(topic.readers, ["助理", "知识库"], "a new topic takes the default readers")
        do {
            _ = try await mock.createTopic(name: "英语", readers: nil)
            XCTFail("a taken name is refused")
        } catch APIError.conflict {}

        let entry = try await mock.uploadKnowledge(
            path: "知识库/英语/单词.pdf", data: Data([1, 2, 3]), contentType: "application/pdf")
        XCTAssertEqual(entry.status, "pending")
        do {
            _ = try await mock.uploadKnowledge(
                path: "知识库/英语/单词.pdf", data: Data(), contentType: "application/pdf")
            XCTFail("an upload never replaces a file")
        } catch APIError.conflict {}
        do {
            _ = try await mock.uploadKnowledge(
                path: "知识库/数据结构/整理/x.md", data: Data(), contentType: "text/markdown")
            XCTFail("the notes folder is the job's")
        } catch APIError.invalidRequest {}

        let removed = try await mock.updateKnowledge(KnowledgePatch(agents: ["Claude Code"]))
        XCTAssertFalse(removed.areas.contains { $0.readers.contains("Codex") })
        let results = try await mock.searchKnowledge("Dijkstra")
        XCTAssertEqual(results.first?.kind, "note")
    }

    func testKnowledgeEndpointsHitTheDocumentedRoutes() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://mini.example.ts.net")!, tokens: StaticTokenProvider("t"),
            session: URLSession(configuration: configuration))
        StubProtocol.requests = []
        StubProtocol.handler = { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            switch (request.httpMethod ?? "", request.url!.path) {
            case ("GET", "/api/v1/knowledge"), ("PATCH", "/api/v1/knowledge"): return (200, Self.overview)
            case ("GET", "/api/v1/knowledge/file"):
                XCTAssertEqual(query, [URLQueryItem(name: "path", value: "知识库/课程/整理/最短路径.md")])
                return (200, Self.note)
            case ("GET", "/api/v1/knowledge/search"):
                return (200, #"{"results": [{"path": "a.md", "name": "a.md", "title": "a", "kind": "file", "area": ".", "excerpt": "…"}]}"#)
            case ("POST", "/api/v1/knowledge/files"):
                XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/pdf")
                return (409, "{}")
            default: return (404, "{}")
            }
        }
        _ = try await client.knowledge()
        _ = try await client.updateKnowledge(KnowledgePatch(defaultReaders: []))
        let patch = try XCTUnwrap(StubProtocol.requests.last.flatMap(StubProtocol.body(of:)))
        XCTAssertEqual(patch, #"{"default_readers":[]}"#)
        _ = try await client.knowledgeFile(path: "知识库/课程/整理/最短路径.md")
        let results = try await client.searchKnowledge("最短")
        XCTAssertEqual(results.count, 1)
        do {
            _ = try await client.uploadKnowledge(path: "知识库/课程/a.pdf", data: Data([1]), contentType: "application/pdf")
            XCTFail("409 is a conflict")
        } catch APIError.conflict {}
    }
}
