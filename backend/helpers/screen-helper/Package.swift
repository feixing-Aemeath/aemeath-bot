// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "screen-helper",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "screen-helper", targets: ["ScreenHelper"])],
    targets: [.executableTarget(name: "ScreenHelper")]
)
