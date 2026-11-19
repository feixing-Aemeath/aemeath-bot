import XCTest

@testable import AssistantCore

final class MarkdownTests: XCTestCase {
    func testReportSectionsBecomeHeadings() {
        let body = "### 睡得怎么样\n昨晚总睡眠 **4小时23分**。\n\n### 今天的训练强度建议\n- 低强度"
        XCTAssertEqual(
            MarkdownLine.lines(body),
            [
                .heading("睡得怎么样"), .text("昨晚总睡眠 **4小时23分**。"), .text(""),
                .heading("今天的训练强度建议"), .text("- 低强度"),
            ])
    }

    func testOnlyRealHeadings() {
        XCTAssertEqual(MarkdownLine.headingTitle("# 标题"), "标题")
        XCTAssertEqual(MarkdownLine.headingTitle("   ## 缩进三格 ##"), "缩进三格")
        XCTAssertEqual(MarkdownLine.headingTitle("###### 六级"), "六级")
        XCTAssertEqual(MarkdownLine.headingTitle("##"), "")
        XCTAssertEqual(MarkdownLine.headingTitle("## C# 入门"), "C# 入门")
        XCTAssertNil(MarkdownLine.headingTitle("#话题 没有空格"))
        XCTAssertNil(MarkdownLine.headingTitle("####### 七个"))
        XCTAssertNil(MarkdownLine.headingTitle("    # 缩进四格是代码"))
        XCTAssertNil(MarkdownLine.headingTitle("第 3 # 不在行首"))
    }

    func testCodeFencesAndLineEndingsAreKept() {
        XCTAssertEqual(
            MarkdownLine.lines("```\n# 注释\n```\n# 标题\r\n正文"),
            [.text("```"), .text("# 注释"), .text("```"), .heading("标题"), .text("正文")])
    }

    func testFixedFormatLines() {
        let digest = "**新待办**\n- 实验室：年度材料，明天 18:00\n\n**通知**：\n* 宿舍楼：下周三停水\n1. 先交表\n2、再签字\n**天气** 阵雨\n睡了 1.5 小时"
        XCTAssertEqual(
            RichLine.lines(digest),
            [
                .section("新待办"), .bullet("实验室：年度材料，明天 18:00"), .blank, .section("通知"),
                .bullet("宿舍楼：下周三停水"), .numbered("1.", "先交表"), .numbered("2.", "再签字"),
                .text("**天气** 阵雨"), .text("睡了 1.5 小时"),
            ])
        XCTAssertFalse(RichLine.isPlain(RichLine.lines(digest)))
        XCTAssertTrue(RichLine.isPlain(RichLine.lines("一行\n\n**粗体** 开头的一行")))
    }

    func testSectionTitlesAreWholeBoldLines() {
        XCTAssertEqual(MarkdownLine.sectionTitle("**还不确定**"), "还不确定")
        XCTAssertEqual(MarkdownLine.sectionTitle("  **通知**："), "通知")
        XCTAssertNil(MarkdownLine.sectionTitle("**天气** 阵雨"))
        XCTAssertNil(MarkdownLine.sectionTitle("****"))
        XCTAssertNil(MarkdownLine.sectionTitle("**a** and **b**"))
    }

    func testFencedLinesStayText() {
        XCTAssertEqual(RichLine.lines("```\n- 不是列表\n```"), [.text("```"), .text("- 不是列表"), .text("```")])
    }

    func testTablesAreRead() {
        let reply = "这周的睡眠：\n\n| 日期 | 时长 | 深睡 |\n|:--|--:|:-:|\n| 周一 | 7.2 小时 | 1.1 |\n| 周二 | 6 小时 |\n\n最好的是周一。"
        XCTAssertEqual(
            RichLine.lines(reply),
            [
                .text("这周的睡眠："), .blank,
                .table(
                    MarkdownTable(
                        header: ["日期", "时长", "深睡"], alignments: [.leading, .trailing, .center],
                        rows: [["周一", "7.2 小时", "1.1"], ["周二", "6 小时", ""]])),
                .blank, .text("最好的是周一。"),
            ])
        XCTAssertFalse(RichLine.isPlain(RichLine.lines(reply)))
    }

    func testTableEdges() {
        // Without the outer bars, a bar inside a cell, and a row with a cell too many.
        XCTAssertEqual(
            RichLine.lines("项目 | 说明\n--- | ---\n管道 | a \\| b\n多 | 一 | 格"),
            [
                .table(
                    MarkdownTable(
                        header: ["项目", "说明"], alignments: [.leading, .leading],
                        rows: [["管道", "a | b"], ["多", "一"]]))
            ])
        // A line of dashes that does not match the header, or none at all, is no table.
        XCTAssertEqual(RichLine.lines("a | b\n---"), [.text("a | b"), .text("---")])
        XCTAssertEqual(RichLine.lines("| a | b |\n|---|"), [.text("| a | b |"), .text("|---|")])
        // A table ends at the first line without a bar; code stays code.
        XCTAssertEqual(
            RichLine.lines("| a |\n|---|\n| 1 |\n之后的话\n```\n| x |\n|---|\n```"),
            [
                .table(MarkdownTable(header: ["a"], alignments: [.leading], rows: [["1"]])),
                .text("之后的话"), .text("```"), .text("| x |"), .text("|---|"), .text("```"),
            ])
    }
}
