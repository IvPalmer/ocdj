// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DJToolsApp",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "DJToolsApp", targets: ["DJToolsApp"]),
    ],
    dependencies: [],
    targets: [
        .executableTarget(
            name: "DJToolsApp",
            dependencies: [],
            path: "Sources"
        ),
    ]
)


