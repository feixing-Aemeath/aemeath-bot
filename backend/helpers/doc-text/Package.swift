// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "doc-text",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "doc-text", targets: ["DocText"])],
    targets: [.executableTarget(name: "DocText")]
)
