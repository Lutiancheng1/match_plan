import SwiftUI

@main
struct MatchPlanRecorderApp: App {
    @State private var controller = AppController()

    var body: some Scene {
        WindowGroup("MatchPlan Recorder") {
            ContentView(controller: controller)
        }
        .defaultSize(width: 1280, height: 820)
    }
}
