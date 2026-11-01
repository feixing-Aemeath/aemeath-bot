// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "apple-sync",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "apple-sync", targets: ["AppleSync"])],
    targets: [.executableTarget(name: "AppleSync")]
)
