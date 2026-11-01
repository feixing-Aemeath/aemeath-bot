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
}
