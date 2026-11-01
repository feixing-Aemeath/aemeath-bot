// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AssistantCore",
    platforms: [.iOS(.v17), .macOS(.v14), .watchOS(.v10)],
    products: [
        // Data models, the HTTP client, Keychain token storage and card parsing (no UI).
        .library(name: "AssistantCore", targets: ["AssistantCore"]),
        // SwiftUI views shared by the iPhone, Mac and Watch apps.
        .library(name: "AssistantUI", targets: ["AssistantUI"]),
    ],
    targets: [
        .target(name: "AssistantCore"),
        .target(name: "AssistantUI", dependencies: ["AssistantCore"]),
        .testTarget(name: "AssistantCoreTests", dependencies: ["AssistantCore"]),
    ]
)
