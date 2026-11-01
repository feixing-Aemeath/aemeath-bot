// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "photo-export",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "photo-export", targets: ["PhotoExport"])],
    targets: [.executableTarget(name: "PhotoExport")]
)
