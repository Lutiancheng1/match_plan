import SwiftUI

@main
struct MatchPlanRecorderApp: App {
    @State private var controller = AppController()

    init() {
        UserDefaults.standard.register(defaults: [
            "AppleShowScrollBars": "WhenScrolling"
        ])
    }

    var body: some Scene {
        WindowGroup("MatchPlan Recorder") {
            ContentView(controller: controller)
        }
        .defaultSize(width: 1280, height: 820)
    }
}
