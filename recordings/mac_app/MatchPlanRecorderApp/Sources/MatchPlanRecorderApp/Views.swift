import AppKit
import SwiftUI

enum SortField: String {
    case time
    case length
}

struct SortState {
    var field: SortField = .time
    var ascending: Bool = false // false = newest/longest first

    mutating func toggle(_ tapped: SortField) {
        if field == tapped {
            ascending.toggle()
        } else {
            field = tapped
            ascending = false
        }
    }

    func timeLabel() -> String {
        field == .time ? (ascending ? "时间 ↑" : "时间 ↓") : "时间"
    }

    func lengthLabel() -> String {
        field == .length ? (ascending ? "时长 ↑" : "时长 ↓") : "时长"
    }
}

struct ContentView: View {
    @Bindable var controller: AppController

    var body: some View {
        NavigationSplitView {
            List {
                Section("控制") {
                    SidebarControlsCard(controller: controller)
                        .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
                        .listRowBackground(Color.clear)
                }
            }
            .navigationTitle("MatchPlan")
        } detail: {
            TabView {
                DashboardView(controller: controller)
                    .tabItem { Text("总览") }
                CaptureSettingsView(controller: controller)
                    .tabItem { Text("录制配置") }
                BrowserPanelView(controller: controller)
                    .tabItem { Text("登录页") }
                DataSitePanelView(controller: controller)
                    .tabItem { Text("数据站") }
                WorkersView(controller: controller)
                    .tabItem { Text("Worker") }
                WorkerHistoryView(controller: controller)
                    .tabItem { Text("历史") }
                ArtifactManagerView(controller: controller)
                    .tabItem { Text("产物") }
                PreviewView(controller: controller)
                    .tabItem { Text("预览") }
                LogsView(controller: controller)
                    .tabItem { Text("日志") }
            }
            .padding()
        }
        .frame(minWidth: 1180, minHeight: 760)
    }
}

private struct SidebarControlsCard: View {
    @Bindable var controller: AppController

    private var isRunning: Bool {
        controller.supervisorStatus.dispatcher_alive || controller.supervisorStatus.alive_worker_count > 0 || !controller.activeWorkers.isEmpty
    }

    private var primaryTitle: String {
        isRunning ? "停止录制链" : "启动录制链"
    }

    private var primarySubtitle: String {
        isRunning ? "当前链路正在运行，点这里优雅停止" : "从这里直接启动正式录制链"
    }

    private var primarySymbol: String {
        isRunning ? "stop.circle.fill" : "play.circle.fill"
    }

    private var primaryTint: Color {
        isRunning ? .red : .accentColor
    }

    private var primaryDisabled: Bool {
        isRunning ? (controller.controlsLocked && controller.pendingActionCommand == "stop") : (!controller.canLaunchRecorder || controller.controlsLocked)
    }

    private var secondaryGrid: [GridItem] {
        [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Label(controller.runtimePhaseTitle, systemImage: isRunning ? "dot.radiowaves.left.and.right" : "moon.zzz.fill")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(isRunning ? Color.green.opacity(0.14) : Color.secondary.opacity(0.12))
                    .clipShape(Capsule())
                if controller.controlsLocked {
                    Label("执行中", systemImage: "clock.arrow.circlepath")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.orange.opacity(0.15))
                        .clipShape(Capsule())
                }
            }

            Button {
                Task {
                    if isRunning {
                        await controller.stopSupervisor()
                    } else {
                        await controller.startSupervisor()
                    }
                }
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: primarySymbol)
                        .font(.system(size: 28, weight: .semibold))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(primaryTitle)
                            .font(.headline.weight(.semibold))
                        Text(primarySubtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(primaryTint.gradient)
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            .buttonStyle(.plain)
            .disabled(primaryDisabled)
            .opacity(primaryDisabled ? 0.6 : 1.0)

            LazyVGrid(columns: secondaryGrid, spacing: 10) {
                SidebarToolButton(
                    title: "刷新",
                    subtitle: "同步状态",
                    symbol: "arrow.clockwise.circle.fill",
                    tint: .blue
                ) {
                    Task { await controller.refreshAll() }
                }

                SidebarToolButton(
                    title: "确保运行",
                    subtitle: "自动拉起",
                    symbol: "bolt.badge.clock.fill",
                    tint: .green
                ) {
                    Task { await controller.ensureRunning() }
                }
                .disabled(!controller.canLaunchRecorder || controller.controlsLocked)

                SidebarToolButton(
                    title: "重启",
                    subtitle: "重建链路",
                    symbol: "arrow.triangle.2.circlepath.circle.fill",
                    tint: .orange
                ) {
                    Task { await controller.restartSupervisor() }
                }
                .disabled(!controller.canLaunchRecorder || controller.controlsLocked)

                SidebarToolButton(
                    title: "状态",
                    subtitle: controller.bridgeSessionReady ? "登录已就绪" : "等待登录",
                    symbol: controller.bridgeSessionReady ? "checkmark.shield.fill" : "exclamationmark.shield.fill",
                    tint: controller.bridgeSessionReady ? .mint : .gray
                ) {}
                .disabled(true)
            }

            if !controller.startupProgressLines.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(controller.startupProgressLines.prefix(4).enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.thinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.white.opacity(0.12), lineWidth: 1)
        )
        .padding(.vertical, 4)
    }
}

private struct SidebarToolButton: View {
    let title: String
    let subtitle: String
    let symbol: String
    let tint: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(tint)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .padding(12)
            .background(Color.white.opacity(0.58))
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .opacity(actionIsMeaningful ? 1.0 : 0.72)
    }

    private var actionIsMeaningful: Bool {
        true
    }
}

struct DashboardView: View {
    @Bindable var controller: AppController
    private let metricColumns = [
        GridItem(.flexible(minimum: 160), spacing: 14),
        GridItem(.flexible(minimum: 160), spacing: 14),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                dashboardHero

                LazyVGrid(columns: metricColumns, spacing: 14) {
                    dashboardMetricCard("运行阶段", controller.runtimePhaseTitle, symbol: "waveform.path.ecg.rectangle.fill", tint: .blue)
                    dashboardMetricCard("活跃录制", "\(controller.supervisorStatus.recording_worker_count)", symbol: "record.circle.fill", tint: .red)
                    dashboardMetricCard("活跃 Worker", "\(controller.supervisorStatus.alive_worker_count)", symbol: "dot.radiowaves.left.and.right", tint: .green)
                    dashboardMetricCard("最近完成", "\(controller.supervisorStatus.recent_finished_count)", symbol: "checkmark.circle.fill", tint: .mint)
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        dashboardMetricCard("总录制时长", controller.totalActiveDurationText, symbol: "clock.fill", tint: .orange)
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        dashboardMetricCard("最长单条时长", controller.maxActiveDurationText, symbol: "timer", tint: .purple)
                    }
                }

                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 16) {
                        dashboardSectionCard(title: "登录与页面状态", symbol: "lock.shield.fill", tint: .teal) {
                            Text("UI 已经内嵌独立网页登录页。")
                            Text(controller.bridgeStatusSummary)
                                .foregroundStyle(controller.bridgePageState.hasLivePane && !controller.bridgePageState.loginRequired ? .green : .orange)
                            dashboardKeyValue("当前页面", controller.bridgePageState.currentURL.isEmpty ? "未知" : controller.bridgePageState.currentURL)
                            dashboardKeyValue("live 候选", "\(controller.bridgePageState.liveCandidateCount)")
                            dashboardKeyValue("需要登录", controller.bridgePageState.loginRequired ? "是" : "否")
                            Text(controller.canLaunchRecorder ? "录制启动条件：已满足" : "录制启动条件：未满足")
                                .foregroundStyle(controller.canLaunchRecorder ? .green : .orange)
                            if !controller.canLaunchRecorder, !controller.launchGuardMessage.isEmpty {
                                Text(controller.launchGuardMessage)
                                    .font(.caption)
                                    .foregroundStyle(.orange)
                            }
                        }

                        dashboardSectionCard(title: "通知状态", symbol: "bell.badge.fill", tint: .pink) {
                            Text(controller.supervisorStatus.notificationsEnabled ? "飞书通知已接入运行链" : "飞书通知当前未启用")
                                .foregroundStyle(controller.supervisorStatus.notificationsEnabled ? .green : .secondary)
                            if controller.supervisorStatus.notificationsEnabled {
                                Text("channel=\(controller.supervisorStatus.notify_channel) | account=\(controller.supervisorStatus.notify_account) | target=\(controller.supervisorStatus.notify_target)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text("新直播 \(controller.supervisorStatus.notify_on_new_live ? "开" : "关") · 开始 \(controller.supervisorStatus.notify_on_recording_started ? "开" : "关") · 完成 \(controller.supervisorStatus.notify_on_recording_completed ? "开" : "关") · 失败 \(controller.supervisorStatus.notify_on_recording_failed ? "开" : "关")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .frame(maxWidth: 360, alignment: .topLeading)

                    VStack(alignment: .leading, spacing: 16) {
                        if !controller.failedWorkers.isEmpty {
                            dashboardSectionCard(title: "最近异常", symbol: "exclamationmark.triangle.fill", tint: .orange) {
                                VStack(alignment: .leading, spacing: 10) {
                                    ForEach(Array(controller.failedWorkers.prefix(5))) { item in
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(item.title)
                                                .font(.subheadline.weight(.semibold))
                                            Text(item.failureSummary.isEmpty ? (item.stopReason.isEmpty ? item.state : item.stopReason) : item.failureSummary)
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                        }
                                        .padding(.vertical, 2)
                                    }
                                }
                            }
                        }

                        dashboardSectionCard(title: "当前策略", symbol: "slider.horizontal.3", tint: .blue) {
                            dashboardKeyValue("模式", controller.settings.mode.title)
                            dashboardKeyValue("球种", controller.settings.gtypes)
                            dashboardKeyValue("发现间隔", "\(controller.settings.discoverIntervalSeconds)s")
                            dashboardKeyValue("监控循环", "\(controller.settings.loopIntervalSeconds)s")
                            dashboardKeyValue("分段时长", "\(controller.settings.segmentMinutes) 分钟")
                            dashboardKeyValue("整场时长", controller.settings.maxDurationMinutes == 0 ? "不限" : "\(controller.settings.maxDurationMinutes) 分钟")
                            dashboardKeyValue("黑屏停录", "\(controller.settings.blackScreenTimeoutSeconds) 秒")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                }

                if controller.failedWorkers.isEmpty {
                    dashboardSectionCard(title: "当前策略", symbol: "slider.horizontal.3", tint: .blue) {
                        dashboardKeyValue("模式", controller.settings.mode.title)
                        dashboardKeyValue("球种", controller.settings.gtypes)
                        dashboardKeyValue("发现间隔", "\(controller.settings.discoverIntervalSeconds)s")
                        dashboardKeyValue("监控循环", "\(controller.settings.loopIntervalSeconds)s")
                        dashboardKeyValue("分段时长", "\(controller.settings.segmentMinutes) 分钟")
                        dashboardKeyValue("整场时长", controller.settings.maxDurationMinutes == 0 ? "不限" : "\(controller.settings.maxDurationMinutes) 分钟")
                        dashboardKeyValue("黑屏停录", "\(controller.settings.blackScreenTimeoutSeconds) 秒")
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 24)
            .padding(.horizontal, 2)
        }
        .background(
            LinearGradient(
                colors: [
                    Color(nsColor: .windowBackgroundColor),
                    Color.accentColor.opacity(0.05),
                    Color(nsColor: .controlBackgroundColor)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
    }

    private var dashboardHero: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("录制总览")
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                    Text(controller.runtimePhaseDetail)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 8) {
                    dashboardStateBadge(
                        title: controller.runtimePhaseTitle,
                        tint: controller.supervisorStatus.recording_worker_count > 0 ? .green : (controller.supervisorStatus.dispatcher_alive ? .blue : .secondary)
                    )
                    if !controller.failedWorkers.isEmpty {
                        dashboardStateBadge(title: "异常 \(controller.failedWorkers.count)", tint: .orange)
                    }
                }
            }

            if !controller.stageCounts.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(controller.stageCounts, id: \.0) { item in
                            Text("\(item.0) \(item.1)")
                                .font(.caption.weight(.semibold))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(Color.white.opacity(0.5))
                                .clipShape(Capsule())
                        }
                    }
                }
            }

            if !controller.lastError.isEmpty {
                dashboardInlineNotice(text: controller.lastError, tint: .red, subdued: false)
            } else if !controller.lastInfo.isEmpty {
                dashboardInlineNotice(text: controller.lastInfo, tint: .secondary, subdued: true)
            }

            if !controller.startupProgressLines.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(controller.startupProgressLines.prefix(5).enumerated()), id: \.offset) { _, line in
                        Label(line, systemImage: "point.topleft.down.curvedto.point.bottomright.up.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            LinearGradient(
                colors: [
                    Color.accentColor.opacity(0.16),
                    Color.white.opacity(0.6),
                    Color(nsColor: .controlBackgroundColor).opacity(0.9)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(Color.white.opacity(0.4), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .shadow(color: .black.opacity(0.05), radius: 18, y: 8)
    }

    func dashboardMetricCard(_ title: String, _ value: String, symbol: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: symbol)
                    .foregroundStyle(tint)
                Text(title)
                    .font(.headline)
                Spacer()
            }
            Text(value)
                .font(.system(size: 28, weight: .bold, design: .rounded))
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.62))
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Color.white.opacity(0.35), lineWidth: 1)
        )
    }

    func dashboardSectionCard<Content: View>(title: String, symbol: String, tint: Color, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label {
                Text(title)
                    .font(.title3.weight(.semibold))
            } icon: {
                Image(systemName: symbol)
                    .foregroundStyle(tint)
            }
            content()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Color.white.opacity(0.2), lineWidth: 1)
        )
    }

    func dashboardKeyValue(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.body)
        }
    }

    func dashboardStateBadge(title: String, tint: Color) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(tint.opacity(0.14))
            .foregroundStyle(tint)
            .clipShape(Capsule())
    }

    func dashboardInlineNotice(text: String, tint: Color, subdued: Bool) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(tint)
                .frame(width: 8, height: 8)
            Text(text)
                .font(.subheadline)
        }
        .foregroundStyle(subdued ? .secondary : tint)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(tint.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

struct CaptureSettingsView: View {
    @Bindable var controller: AppController
    @State private var draft = RecorderAppSettings()
    @State private var isLoaded = false
    @State private var localFeedback = ""
    private let gtypePresets: [(String, String)] = [
        ("足球", "FT"),
        ("足球+篮球", "FT,BK"),
        ("全部常用", "FT,BK,ES,TN,VB,BM,TT,BS,SK,OP"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("录制配置")
                                .font(.title2.bold())
                            Text("先在这里改草稿，再决定保存或保存并重启。")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(isDirty ? "未保存修改" : "已同步")
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(isDirty ? Color.orange.opacity(0.15) : Color.green.opacity(0.15))
                            .foregroundStyle(isDirty ? .orange : .green)
                            .clipShape(Capsule())
                    }

                    HStack(spacing: 12) {
                        Button("恢复已保存") {
                            draft = controller.settings
                            localFeedback = "已恢复到上次保存的配置"
                        }
                        .disabled(!isDirty)

                        Button("保存配置") {
                            controller.applySettings(draft, message: "配置已保存到 App")
                            localFeedback = "配置已保存"
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!isDirty)

                        Button("保存并重启录制") {
                            controller.applySettings(draft, message: "配置已保存，准备重启录制")
                            localFeedback = "配置已保存，正在重启录制"
                            Task { await controller.restartSupervisor() }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!controller.bridgeSessionReady || controller.isBusy)
                    }

                    if !localFeedback.isEmpty || !controller.lastInfo.isEmpty {
                        Text(localFeedback.isEmpty ? controller.lastInfo : localFeedback)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                settingsCard("模式与分类") {
                    Picker("录制模式", selection: $draft.mode) {
                        ForEach(RecorderMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)

                    Text(draft.mode.description)
                        .foregroundStyle(.secondary)

                    VStack(alignment: .leading, spacing: 8) {
                        Text("比赛分类")
                            .font(.headline)
                        TextField("例如：FT 或 FT,BK,ES", text: $draft.gtypes)
                            .textFieldStyle(.roundedBorder)
                        Text("用逗号分隔球种代码。比如 `FT`、`FT,BK`。")
                            .foregroundStyle(.secondary)
                        FlowButtonRow(items: gtypePresets) { _, value in
                            draft.gtypes = value
                        }
                    }
                }

                settingsCard("核心参数") {
                    NumericFieldRow(title: "发现间隔", suffix: "秒", value: $draft.discoverIntervalSeconds)
                    FlowButtonRow(items: [("5分钟", "300"), ("10分钟", "600"), ("15分钟", "900"), ("30分钟", "1800")]) { _, value in
                        draft.discoverIntervalSeconds = Int(value) ?? draft.discoverIntervalSeconds
                    }

                    NumericFieldRow(title: "监听循环", suffix: "秒", value: $draft.loopIntervalSeconds)
                    FlowButtonRow(items: [("1秒", "1"), ("2秒", "2"), ("5秒", "5")]) { _, value in
                        draft.loopIntervalSeconds = Int(value) ?? draft.loopIntervalSeconds
                    }

                    NumericFieldRow(title: "分段时长", suffix: "分钟", value: $draft.segmentMinutes)
                    FlowButtonRow(items: [("1分钟", "1"), ("3分钟", "3"), ("5分钟", "5"), ("10分钟", "10")]) { _, value in
                        draft.segmentMinutes = Int(value) ?? draft.segmentMinutes
                    }

                    NumericFieldRow(title: "整场时长", suffix: "分钟，0=不限", value: $draft.maxDurationMinutes)
                    NumericFieldRow(title: "黑屏停录", suffix: "秒", value: $draft.blackScreenTimeoutSeconds)
                    FlowButtonRow(items: [("180秒", "180"), ("240秒", "240"), ("300秒", "300")]) { _, value in
                        draft.blackScreenTimeoutSeconds = Int(value) ?? draft.blackScreenTimeoutSeconds
                    }
                    NumericFieldRow(title: "预开赛窗口", suffix: "分钟", value: $draft.prestartMinutes)
                    NumericFieldRow(title: "最大并发", suffix: "路，0=不限", value: $draft.maxStreams)
                }

                settingsCard("画质与码率") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("归档视频")
                            .font(.headline)
                        HStack {
                            NumericFieldRow(title: "宽", suffix: "px", value: $draft.archiveWidth)
                            NumericFieldRow(title: "高", suffix: "px", value: $draft.archiveHeight)
                        }
                        NumericFieldRow(title: "归档码率", suffix: "kbps", value: $draft.archiveBitrateKbps)
                        FlowButtonRow(items: [("640x360", "640x360"), ("960x540", "960x540"), ("1280x720", "1280x720")]) { _, value in
                            let parts = value.split(separator: "x")
                            if parts.count == 2 {
                                draft.archiveWidth = Int(parts[0]) ?? draft.archiveWidth
                                draft.archiveHeight = Int(parts[1]) ?? draft.archiveHeight
                            }
                        }
                        FlowButtonRow(items: [("3500", "3500"), ("5000", "5000"), ("6500", "6500")]) { _, value in
                            draft.archiveBitrateKbps = Int(value) ?? draft.archiveBitrateKbps
                        }
                    }

                    Divider()

                    VStack(alignment: .leading, spacing: 8) {
                        Text("HLS 预览")
                            .font(.headline)
                        HStack {
                            NumericFieldRow(title: "宽", suffix: "px", value: $draft.hlsWidth)
                            NumericFieldRow(title: "高", suffix: "px", value: $draft.hlsHeight)
                        }
                        NumericFieldRow(title: "预览码率", suffix: "kbps", value: $draft.hlsBitrateKbps)
                        FlowButtonRow(items: [("640x360", "640x360"), ("960x540", "960x540"), ("1280x720", "1280x720")]) { _, value in
                            let parts = value.split(separator: "x")
                            if parts.count == 2 {
                                draft.hlsWidth = Int(parts[0]) ?? draft.hlsWidth
                                draft.hlsHeight = Int(parts[1]) ?? draft.hlsHeight
                            }
                        }
                        FlowButtonRow(items: [("2500", "2500"), ("3500", "3500"), ("5000", "5000")]) { _, value in
                            draft.hlsBitrateKbps = Int(value) ?? draft.hlsBitrateKbps
                        }
                    }
                }

                settingsCard("运行行为") {
                    Toggle("保留长期监听", isOn: $draft.keepRunning)
                    Toggle("启用 HLS 预览", isOn: $draft.previewEnabled)
                }

                settingsCard("通知") {
                    Toggle("推送到飞书", isOn: $draft.notifications.pushToFeishu)
                    TextField("群聊 / target", text: $draft.notifications.target)
                        .textFieldStyle(.roundedBorder)
                    HStack {
                        TextField("channel", text: $draft.notifications.channel)
                            .textFieldStyle(.roundedBorder)
                        TextField("account", text: $draft.notifications.account)
                            .textFieldStyle(.roundedBorder)
                    }
                    Toggle("新直播提示", isOn: $draft.notifications.pushOnNewLive)
                    Toggle("开始录制提示", isOn: $draft.notifications.pushOnRecordingStarted)
                    Toggle("完成提示", isOn: $draft.notifications.pushOnRecordingCompleted)
                    Toggle("失败提示", isOn: $draft.notifications.pushOnRecordingFailed)
                    Divider()
                    Text(controller.supervisorStatus.notificationsEnabled ? "当前运行链已接入飞书通知" : "当前运行链还没启用飞书通知；保存并重启后才会把这里的通知配置带进运行中的 supervisor。")
                        .font(.caption)
                        .foregroundStyle(controller.supervisorStatus.notificationsEnabled ? .green : .secondary)
                    if controller.supervisorStatus.notificationsEnabled {
                        Text("运行中：\(controller.supervisorStatus.notify_channel) / \(controller.supervisorStatus.notify_account) / \(controller.supervisorStatus.notify_target)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.trailing, 6)
        }
        .onAppear {
            guard !isLoaded else { return }
            draft = controller.settings
            isLoaded = true
        }
        .onChange(of: controller.settings) { _, newValue in
            if !isDirty {
                draft = newValue
            }
        }
    }

    private var isDirty: Bool {
        draft != controller.settings
    }
}

struct NumericFieldRow: View {
    let title: String
    let suffix: String
    @Binding var value: Int

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                Text(suffix)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            TextField(title, value: $value, format: .number)
                .textFieldStyle(.roundedBorder)
                .frame(width: 130)
                .multilineTextAlignment(.trailing)
        }
    }
}

struct FlowButtonRow: View {
    let items: [(String, String)]
    let action: (String, String) -> Void

    var body: some View {
        HStack(spacing: 8) {
            ForEach(items, id: \.0) { item in
                Button(item.0) {
                    action(item.0, item.1)
                }
                .buttonStyle(.bordered)
            }
        }
    }
}

@ViewBuilder
func settingsCard<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
    VStack(alignment: .leading, spacing: 12) {
        Text(title)
            .font(.headline)
        content()
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(.thinMaterial)
    .clipShape(RoundedRectangle(cornerRadius: 16))
}

struct BrowserPanelView: View {
    @Bindable var controller: AppController

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("内嵌登录页")
                    .font(.title3.bold())
                Spacer()
                Button("清除缓存") {
                    Task {
                        await controller.clearWebsiteData()
                        await controller.refreshEmbeddedSession()
                    }
                }
                Button("刷新") {
                    Task { await controller.refreshEmbeddedSession() }
                }
                Button("在默认浏览器打开") {
                    NSWorkspace.shared.open(controller.schedulesURL)
                }
            }
            Text("这张页面是 App 自己的独立网页登录页，不会干扰你外部浏览器的标签和操作。")
                .foregroundStyle(.secondary)
            Text(controller.bridgePageState.hasLivePane && !controller.bridgePageState.loginRequired
                 ? "后端已接入这张内嵌页的登录态。"
                 : "这张内嵌页已常驻，但当前还没拿到 live 列表，请先在这里登录。")
                .foregroundStyle(controller.bridgePageState.hasLivePane && !controller.bridgePageState.loginRequired ? .green : .orange)
            Text("本地 bridge：\(controller.bridgeBaseURL.absoluteString)")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("当前 URL：\(controller.bridgePageState.currentURL.isEmpty ? "未知" : controller.bridgePageState.currentURL)")
                .font(.caption)
                .foregroundStyle(.secondary)
            if !controller.bridgePageState.error.isEmpty {
                Text("bridge 状态：\(controller.bridgePageState.error)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            RecorderWebView(url: controller.schedulesURL, onReady: { webView in
                controller.registerAppWebView(webView, preferred: true)
            })
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
}

struct DataSitePanelView: View {
    @Bindable var controller: AppController

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("源站数据页")
                    .font(.title3.bold())
                Spacer()
                if controller.dataSiteProxyReady {
                    Button("刷新") {
                        controller.dataSiteProxyReady = false
                        controller.ensureDataSiteProxy()
                    }
                }
            }
            Text("自动登录数据站，通过本地代理处理 SSL 证书。")
                .foregroundStyle(.secondary)
            if controller.dataSiteProxyReady {
                RecorderWebView(url: controller.dataEntryURL)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .id("data-site-\(controller.dataSiteProxyReady)")
            } else {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("正在启动数据站本地代理...")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task {
            controller.ensureDataSiteProxy()
            // Poll until ready (proxy may need a few seconds to start)
            for _ in 0..<30 {
                if controller.dataSiteProxyReady { break }
                try? await Task.sleep(for: .seconds(1))
                controller.ensureDataSiteProxy()
            }
        }
    }
}

struct WorkersView: View {
    @Bindable var controller: AppController

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("当前活跃 Worker")
                        .font(.title3.bold())
                    Spacer()
                    Button("刷新 Worker") {
                        controller.refreshWorkerStatuses()
                    }
                }
                List(selection: $controller.selectedWorkerID) {
                    ForEach(controller.activeWorkers) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(item.title)
                            HStack(spacing: 8) {
                                Text(item.stageTitle)
                                if !item.startedAt.isEmpty {
                                    if controller.workerHasTerminalState(item) {
                                        Text("已录 \(controller.workerDurationText(item))")
                                    } else {
                                        TimelineView(.periodic(from: .now, by: 1)) { _ in
                                            Text("已录 \(controller.workerDurationText(item))")
                                        }
                                    }
                                }
                                if item.activeSegments > 0 || item.hlsSegmentCount > 0 {
                                    Text("seg \(item.activeSegments) / hls \(item.hlsSegmentCount)")
                                }
                                if item.effectiveFPS > 0 {
                                    Text(String(format: "近8秒 fps %.1f", item.effectiveFPS))
                                        .foregroundStyle(item.lowFrameRate ? .orange : .secondary)
                                }
                            }
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .tag(Optional(item.id))
                    }
                }
            }
            .frame(minWidth: 320, maxWidth: 360)

            VStack(alignment: .leading, spacing: 12) {
                Text("Worker 详情")
                    .font(.title3.bold())
                if let item = controller.selectedWorker {
                    workerDetail(item)
                } else {
                    Text("暂无活跃 Worker")
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }

    @ViewBuilder
    func workerDetail(_ item: WorkerStateSummary) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Text(item.title).font(.headline)
                Text("阶段：\(item.stageTitle)")
                Text("状态码：\(item.state)")
                Text("停止原因：\(item.stopReason.isEmpty ? "无" : item.stopReason)")
                Text("更新时间：\(formattedWorkerTimestamp(item.updatedAt))")
                if !item.startedAt.isEmpty {
                    if controller.workerHasTerminalState(item) {
                        Text("录制时长：\(controller.workerDurationText(item))")
                    } else {
                        TimelineView(.periodic(from: .now, by: 1)) { _ in
                            Text("录制时长：\(controller.workerDurationText(item))")
                        }
                    }
                }
                Text("匹配数据行数：\(item.matchedRows)")
                if !item.serverHost.isEmpty {
                    Text("接流主机：\(item.serverHost)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text("连接状态：\(item.connected ? "已连接" : "未连接") | 归档段：\(item.activeSegments) | HLS片段：\(item.hlsSegmentCount)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !item.videoCodec.isEmpty || !item.audioCodec.isEmpty {
                    Text("编解码：video=\(item.videoCodec.isEmpty ? "-" : item.videoCodec) | audio=\(item.audioCodec.isEmpty ? "-" : item.audioCodec)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if item.effectiveFPS > 0 {
                    Text(String(format: "当前近8秒有效帧率：%.1f fps%@", item.effectiveFPS, item.lowFrameRate ? " | 低帧告警" : ""))
                        .font(.caption)
                        .foregroundStyle(item.lowFrameRate ? .orange : .secondary)
                }
                if !item.lastPacketAt.isEmpty {
                    Text("最后收包：\(formattedWorkerTimestamp(item.lastPacketAt))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !item.league.isEmpty {
                    Text("联赛：\(item.league)")
                }
                if !item.gid.isEmpty || !item.ecid.isEmpty || !item.hgid.isEmpty {
                    Text("源站标识：gid=\(item.gid.isEmpty ? "-" : item.gid) | ecid=\(item.ecid.isEmpty ? "-" : item.ecid) | hgid=\(item.hgid.isEmpty ? "-" : item.hgid)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !item.note.isEmpty {
                    Text("备注：\(item.note)")
                }
                if !item.failureSummary.isEmpty {
                    Text("失败详情：\(item.failureSummary)")
                        .foregroundStyle(.red)
                }
                if item.state == "recording" && item.lastPacketAt.isEmpty && item.activeSegments == 0 && item.hlsSegmentCount == 0 {
                    Text("状态提示：已起 worker，但还没开始真正收包。")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                if let dataFileURL = item.dataFileURL {
                    Text("数据文件：\(dataFileURL.path)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text("目录：\(item.sessionDir)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    if let previewURL = item.previewURL {
                        Button("打开 HLS") { NSWorkspace.shared.open(previewURL) }
                    }
                    if let mergedVideoURL = item.mergedVideoURL {
                        Button("打开成片") { NSWorkspace.shared.open(mergedVideoURL) }
                    }
                    if let dataFileURL = item.dataFileURL {
                        Button("打开数据文件") { NSWorkspace.shared.open(dataFileURL) }
                    }
                    Button("打开目录") {
                        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: item.sessionDir)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    func formattedWorkerTimestamp(_ value: String) -> String {
        let raw = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return "-" }
        if let date = parseISODate(raw) {
            let localText = date.formatted(date: .abbreviated, time: .standard)
            let china = DateFormatter()
            china.locale = Locale(identifier: "zh_CN")
            china.timeZone = TimeZone(identifier: "Asia/Shanghai")
            china.dateFormat = "yyyy-MM-dd HH:mm:ss"
            return "\(localText) | 中国 \(china.string(from: date))"
        }
        return raw
    }
    func parseISODate(_ value: String) -> Date? {
        let raw = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: raw) {
            return date
        }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractional.date(from: raw) {
            return date
        }
        let localFractional = DateFormatter()
        localFractional.locale = Locale(identifier: "en_US_POSIX")
        localFractional.timeZone = .current
        localFractional.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        if let date = localFractional.date(from: raw) {
            return date
        }
        let localPlain = DateFormatter()
        localPlain.locale = Locale(identifier: "en_US_POSIX")
        localPlain.timeZone = .current
        localPlain.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return localPlain.date(from: raw)
    }
}

struct WorkerHistoryView: View {
    @Bindable var controller: AppController
    @State private var sort = SortState()

    private var sortedHistoricalWorkers: [WorkerStateSummary] {
        let items = controller.historicalWorkers
        switch sort.field {
        case .time:
            return items.sorted { sort.ascending ? $0.startedAtEpoch < $1.startedAtEpoch : $0.startedAtEpoch > $1.startedAtEpoch }
        case .length:
            return items.sorted { sort.ascending ? $0.sortDurationSeconds < $1.sortDurationSeconds : $0.sortDurationSeconds > $1.sortDurationSeconds }
        }
    }

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("历史 Worker")
                        .font(.title3.bold())
                    Spacer()
                    sortButton(sort.timeLabel(), field: .time)
                    sortButton(sort.lengthLabel(), field: .length)
                    Button("删除所选历史") {
                        Task { await controller.deleteSelectedHistoryArtifacts() }
                    }
                    .disabled(!controller.canDeleteSelectedHistory)
                    Button("刷新历史") {
                        controller.refreshWorkerStatuses()
                    }
                }
                Text("已选 \(controller.selectedHistoricalWorkers.count) 条，涉及 \(controller.selectedHistoricalSessionDirs.count) 个 session")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                List(selection: $controller.selectedHistoryWorkerID) {
                    ForEach(sortedHistoricalWorkers) { item in
                        HStack(alignment: .top, spacing: 10) {
                            Toggle("", isOn: Binding(
                                get: { controller.selectedHistoryWorkerIDs.contains(item.id) },
                                set: { _ in
                                    if controller.selectedHistoryWorkerIDs.contains(item.id) {
                                        controller.selectedHistoryWorkerIDs.remove(item.id)
                                    } else {
                                        controller.selectedHistoryWorkerIDs.insert(item.id)
                                    }
                                }
                            ))
                            .labelsHidden()
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.title)
                                HStack(spacing: 8) {
                                    Text(item.stageTitle)
                                    if !item.stopReason.isEmpty {
                                        Text(item.stopReason)
                                    }
                                }
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                        .tag(Optional(item.id))
                    }
                }
            }
            .frame(minWidth: 320, maxWidth: 360)

            VStack(alignment: .leading, spacing: 12) {
                Text("历史详情")
                    .font(.title3.bold())
                if !controller.selectedHistoricalSessionDirs.isEmpty {
                    Text("将删除这些历史记录对应的 session 目录和状态文件，不影响当前活跃录制。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let item = controller.selectedHistoryWorker {
                    WorkersView(controller: controller).workerDetail(item)
                } else {
                    Text("暂无历史记录")
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
        }
    }

    private func sortButton(_ label: String, field: SortField) -> some View {
        Button(label) { sort.toggle(field) }
            .buttonStyle(.bordered)
            .tint(sort.field == field ? .accentColor : .secondary)
    }
}

struct ArtifactManagerView: View {
    @Bindable var controller: AppController
    @State private var sort = SortState()
    @State private var showCleanupConfirmStepOne = false
    @State private var showCleanupConfirmStepTwo = false

    private var sortedArtifacts: [ArtifactSessionSummary] {
        let items = controller.artifacts
        switch sort.field {
        case .time:
            return items.sorted { sort.ascending ? ($0.date, $0.session_name) < ($1.date, $1.session_name) : ($0.date, $0.session_name) > ($1.date, $1.session_name) }
        case .length:
            return items.sorted { sort.ascending ? $0.segment_count < $1.segment_count : $0.segment_count > $1.segment_count }
        }
    }

    var body: some View {
        HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("产物管理")
                        .font(.title3.bold())
                    Spacer()
                    sortButton(sort.timeLabel(), field: .time)
                    sortButton(sort.lengthLabel(), field: .length)
                    Text("共 \(controller.artifacts.count) 项")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("刷新产物") {
                        Task { await controller.refreshArtifacts() }
                    }
                }
                Text("支持多选删除。正在录制的条目默认禁直接删，可使用“停止后删除”。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if controller.artifacts.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("当前没有显示任何产物")
                            .font(.subheadline.weight(.semibold))
                        Text("如果你刚打开 App，这里可能还没刷新到。可以点“刷新产物”，或者切到这个页签后稍等一下。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if !controller.lastError.isEmpty {
                            Text("最近错误：\(controller.lastError)")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(12)
                    .background(.thinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                } else {
                    List {
                        ForEach(sortedArtifacts) { item in
                            ArtifactRow(
                                item: item,
                                selected: controller.selectedArtifactIDs.contains(item.id),
                                toggle: {
                                    if controller.selectedArtifactIDs.contains(item.id) {
                                        controller.selectedArtifactIDs.remove(item.id)
                                    } else {
                                        controller.selectedArtifactIDs.insert(item.id)
                                    }
                                }
                            )
                        }
                    }
                }
            }
            .frame(minWidth: 420, maxWidth: 500)

            VStack(alignment: .leading, spacing: 12) {
                Text("选中摘要")
                    .font(.title3.bold())
                Text("已选 \(controller.selectedArtifacts.count) 项 | 已结束 \(controller.selectedEndedArtifacts.count) | 正在录制 \(controller.selectedActiveArtifacts.count)")
                    .foregroundStyle(.secondary)
                HStack(spacing: 12) {
                    Button("删除所选已结束") {
                        Task { await controller.deleteSelectedArtifacts(stopActive: false) }
                    }
                    .disabled(!controller.canDeleteSelectedDirectly)
                    .buttonStyle(.borderedProminent)

                    Button("停止后删除所选") {
                        Task { await controller.deleteSelectedArtifacts(stopActive: true) }
                    }
                    .disabled(!controller.canStopAndDeleteSelected)
                    .buttonStyle(.bordered)
                }
                Divider()
                VStack(alignment: .leading, spacing: 10) {
                    Text("无数据清理与归档")
                        .font(.headline)
                    Text("短的无数据垃圾 session 会直接删除；长视频但没有本地数据的 session 会归档到单独目录，并写 README 和 inventory。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    HStack(spacing: 12) {
                        Button("预览清理归档") {
                            Task { await controller.previewCleanupNoDataSessions() }
                        }
                        .buttonStyle(.bordered)

                        Button("执行清理归档") {
                            showCleanupConfirmStepOne = true
                        }
                        .disabled(controller.noDataCleanupPreview == nil)
                        .buttonStyle(.borderedProminent)
                    }
                    if let preview = controller.noDataCleanupPreview {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("本次将删除 \(preview.deleteCount) 个垃圾 session，归档 \(preview.archiveCount) 个长视频无数据 session，跳过活跃录制 \(preview.activeSkippedCount) 个。")
                                .font(.caption)
                            Text("归档目录：\(preview.archiveRoot)")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                            if !preview.deleteCandidates.isEmpty {
                                Text("将删除：\(preview.deleteCandidates.joined(separator: "、"))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if !preview.archiveCandidates.isEmpty {
                                Text("将归档：\(preview.archiveCandidates.joined(separator: "、"))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.thinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
                if !controller.selectedActiveArtifacts.isEmpty {
                    Text("包含正在录制条目：直接删除已禁用。使用“停止后删除所选”会先停止对应任务，再删除这些目录和状态文件。")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(controller.selectedArtifacts) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayTitle)
                                    .font(.subheadline.weight(.semibold))
                                Text("\(item.modeTitle) | \(item.date) | seg \(item.segment_count) | full \(item.full_video_count) | HLS \(item.has_hls ? "有" : "无")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(item.active ? "状态：正在录制 / \(item.active_state)" : "状态：已结束，可直接删除")
                                    .font(.caption)
                                    .foregroundStyle(item.active ? .orange : .green)
                                Text(item.session_dir)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.thinMaterial)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                    }
                }
                Spacer()
            }
        }
        .task {
            if controller.artifacts.isEmpty {
                await controller.refreshArtifacts()
            }
        }
        .confirmationDialog(
            "确认第一步",
            isPresented: $showCleanupConfirmStepOne,
            titleVisibility: .visible
        ) {
            Button("继续下一步", role: .destructive) {
                showCleanupConfirmStepTwo = true
            }
            Button("取消", role: .cancel) {}
        } message: {
            if let preview = controller.noDataCleanupPreview {
                Text("本次将删除 \(preview.deleteCount) 个无数据垃圾 session，并归档 \(preview.archiveCount) 个长视频无数据 session。")
            } else {
                Text("请先预览清理归档结果。")
            }
        }
        .alert(
            "最后确认执行清理归档",
            isPresented: $showCleanupConfirmStepTwo
        ) {
            Button("执行", role: .destructive) {
                Task { await controller.executeCleanupNoDataSessions() }
            }
            Button("取消", role: .cancel) {}
        } message: {
            if let preview = controller.noDataCleanupPreview {
                Text("将删除 \(preview.deleteCount) 个垃圾 session，并把 \(preview.archiveCount) 个长视频无数据 session 归档到：\n\(preview.archiveRoot)")
            } else {
                Text("请先预览清理归档结果。")
            }
        }
    }

    private func sortButton(_ label: String, field: SortField) -> some View {
        Button(label) { sort.toggle(field) }
            .buttonStyle(.bordered)
            .tint(sort.field == field ? .accentColor : .secondary)
    }
}

struct ArtifactRow: View {
    let item: ArtifactSessionSummary
    let selected: Bool
    let toggle: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Toggle("", isOn: Binding(
                get: { selected },
                set: { _ in toggle() }
            ))
            .labelsHidden()
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.displayTitle)
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(item.modeTitle)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background((item.mode == "formal" ? Color.blue : Color.orange).opacity(0.12))
                        .clipShape(Capsule())
                }
                Text("\(item.date) · seg \(item.segment_count) · full \(item.full_video_count) · HLS \(item.has_hls ? "有" : "无")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(item.active ? "正在录制 · \(item.active_state)" : "已结束")
                    .font(.caption)
                    .foregroundStyle(item.active ? .orange : .green)
            }
        }
        .padding(.vertical, 4)
    }
}

struct PreviewView: View {
    @Bindable var controller: AppController
    @State private var showDataPanel = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("HLS 预览")
                    .font(.title3.bold())
                Spacer()
                Toggle("数据页", isOn: $showDataPanel)
                    .toggleStyle(.switch)
                    .frame(width: 110)
            }
            if let worker = controller.selectedWorker {
                Text("当前选中：\(worker.title)")
                if showDataPanel {
                    DataInspectorView(worker: worker)
                } else {
                    previewPanel(worker: worker)
                }
            } else {
                Text("没有可预览的 worker。")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    func previewPanel(worker: WorkerStateSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("当前版本先以稳定为先，App 内不直接挂播放器，避免预览页把应用拖崩。")
                .foregroundStyle(.secondary)
            if let previewURL = worker.previewURL {
                Text("HLS 预览：\(previewURL.path)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text("当前选中的 worker 还没有 HLS 预览地址。")
                    .foregroundStyle(.secondary)
            }
            if let mergedVideoURL = worker.mergedVideoURL {
                Text("成片：\(mergedVideoURL.path)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                if let previewURL = worker.previewURL {
                    Button("用默认播放器打开 HLS") {
                        NSWorkspace.shared.open(previewURL)
                    }
                }
                if let mergedVideoURL = worker.mergedVideoURL {
                    Button("打开成片") {
                        NSWorkspace.shared.open(mergedVideoURL)
                    }
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct DataInspectorView: View {
    let worker: WorkerStateSummary
    @State private var content = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let dataFileURL = worker.dataFileURL {
                Text(dataFileURL.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    Button("刷新数据") {
                        load()
                    }
                    Button("打开数据文件") {
                        NSWorkspace.shared.open(dataFileURL)
                    }
                }
                TextEditor(text: .constant(content))
                    .font(.system(.caption, design: .monospaced))
                    .frame(minHeight: 420)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .onAppear {
                        load()
                    }
            } else {
                Text("当前这条 worker 还没有关联到单场数据文件。")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func load() {
        guard let dataFileURL = worker.dataFileURL else {
            content = ""
            return
        }
        let text = (try? String(contentsOf: dataFileURL, encoding: .utf8)) ?? ""
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
        let tail = lines.suffix(80).joined(separator: "\n")
        content = tail.isEmpty ? "数据文件当前为空" : tail
    }
}

enum LogCategory: String, CaseIterable, Identifiable {
    case all = "全部"
    case dispatcher = "调度"
    case worker = "Worker"
    case supervisor = "Supervisor"
    case dataSiteProxy = "数据站代理"
    case singbox = "sing-box"
    case app = "应用"

    var id: String { rawValue }
}

struct LogsView: View {
    @Bindable var controller: AppController
    @State private var filter: LogCategory = .all

    private var filteredText: String {
        var lines: [AppLogLine] = []
        switch filter {
        case .all:
            lines = controller.dispatcherLogLines + controller.selectedWorkerLogLines
                + controller.supervisorWrapperLogLines + controller.dataSiteProxyLogLines
                + controller.singboxLogLines + controller.logLines
        case .dispatcher: lines = controller.dispatcherLogLines
        case .worker: lines = controller.selectedWorkerLogLines
        case .supervisor: lines = controller.supervisorWrapperLogLines
        case .dataSiteProxy: lines = controller.dataSiteProxyLogLines
        case .singbox: lines = controller.singboxLogLines
        case .app: lines = controller.logLines
        }
        return joinedLogText(lines, includeTimestamp: filter == .app || filter == .all)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("日志")
                    .font(.title3.bold())
                Spacer()
            }
            HStack(spacing: 8) {
                ForEach(LogCategory.allCases) { cat in
                    let count = logCount(for: cat)
                    Button {
                        filter = (filter == cat) ? .all : cat
                    } label: {
                        Text("\(cat.rawValue) \(count)")
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(filter == cat ? Color.accentColor.opacity(0.2) : Color.secondary.opacity(0.1))
                            .foregroundStyle(filter == cat ? .primary : .secondary)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }

            TextEditor(text: .constant(filteredText.isEmpty ? "暂无日志" : filteredText))
                .font(.system(.body, design: .monospaced))
                .frame(maxHeight: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    private func logCount(for cat: LogCategory) -> Int {
        switch cat {
        case .all:
            return controller.dispatcherLogLines.count + controller.selectedWorkerLogLines.count
                + controller.supervisorWrapperLogLines.count + controller.dataSiteProxyLogLines.count
                + controller.singboxLogLines.count + controller.logLines.count
        case .dispatcher: return controller.dispatcherLogLines.count
        case .worker: return controller.selectedWorkerLogLines.count
        case .supervisor: return controller.supervisorWrapperLogLines.count
        case .dataSiteProxy: return controller.dataSiteProxyLogLines.count
        case .singbox: return controller.singboxLogLines.count
        case .app: return controller.logLines.count
        }
    }

    private func joinedLogText(_ lines: [AppLogLine], includeTimestamp: Bool) -> String {
        lines.map { line in
            if includeTimestamp {
                return "[\(line.timestamp.formatted(date: .omitted, time: .standard))] \(line.text)"
            }
            return line.text
        }
        .joined(separator: "\n")
    }
}

private func parseISODateStatic(_ value: String) -> Date? {
    let raw = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !raw.isEmpty else { return nil }
    let plain = ISO8601DateFormatter()
    if let date = plain.date(from: raw) { return date }
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: raw) { return date }
    let localFractional = DateFormatter()
    localFractional.locale = Locale(identifier: "en_US_POSIX")
    localFractional.timeZone = .current
    localFractional.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
    if let date = localFractional.date(from: raw) { return date }
    let localPlain = DateFormatter()
    localPlain.locale = Locale(identifier: "en_US_POSIX")
    localPlain.timeZone = .current
    localPlain.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return localPlain.date(from: raw)
}
