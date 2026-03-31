// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MatchPlanRecorderApp",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "MatchPlanRecorderApp", targets: ["MatchPlanRecorderApp"]),
    ],
    targets: [
        .executableTarget(
            name: "MatchPlanRecorderApp",
            path: "Sources/MatchPlanRecorderApp"
        ),
    ]
)
