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
                    Button("刷新状态") { Task { await controller.refreshAll() } }
                    Button("启动") { Task { await controller.startSupervisor() } }
                        .disabled(!controller.canLaunchRecorder || controller.controlsLocked)
                    Button("确保运行") { Task { await controller.ensureRunning() } }
                        .disabled(!controller.canLaunchRecorder || controller.controlsLocked)
                    Button("重启") { Task { await controller.restartSupervisor() } }
                        .disabled(!controller.canLaunchRecorder || controller.controlsLocked)
                    Button("停止") { Task { await controller.stopSupervisor() } }
                        .disabled(controller.controlsLocked && controller.pendingActionCommand == "stop")
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(controller.startupProgressLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 4)
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

struct DashboardView: View {
    @Bindable var controller: AppController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("录制总览")
                    .font(.largeTitle.bold())
                VStack(spacing: 16) {
                    HStack(spacing: 16) {
                        statusCard("运行阶段", controller.runtimePhaseTitle)
                        statusCard("活跃录制", "\(controller.supervisorStatus.recording_worker_count)")
                        statusCard("活跃 Worker", "\(controller.supervisorStatus.alive_worker_count)")
                        statusCard("最近完成", "\(controller.supervisorStatus.recent_finished_count)")
                    }
                    HStack(spacing: 16) {
                        TimelineView(.periodic(from: .now, by: 1)) { _ in
                            statusCard("总录制时长", controller.totalActiveDurationText)
                        }
                        TimelineView(.periodic(from: .now, by: 1)) { _ in
                            statusCard("最长单条时长", controller.maxActiveDurationText)
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("当前运行说明")
                        .font(.title3.bold())
                    Text(controller.runtimePhaseDetail)
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(controller.startupProgressLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    if !controller.failedWorkers.isEmpty {
                        Text("最近异常 \(controller.failedWorkers.count) 条")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.orange)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.thinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 16))
                if !controller.stageCounts.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("运行阶段")
                            .font(.title3.bold())
                        HStack(spacing: 10) {
                            ForEach(controller.stageCounts, id: \.0) { item in
                                Text("\(item.0) \(item.1)")
                                    .font(.caption.weight(.semibold))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(Color.secondary.opacity(0.12))
                                    .clipShape(Capsule())
                            }
                        }
                    }
                }
                if !controller.failedWorkers.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("最近异常")
                            .font(.title3.bold())
                        ForEach(Array(controller.failedWorkers.prefix(5))) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.title)
                                    .font(.subheadline.weight(.semibold))
                                Text(item.failureSummary.isEmpty ? (item.stopReason.isEmpty ? item.state : item.stopReason) : item.failureSummary)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.thinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                }
                if !controller.lastError.isEmpty {
                    Text(controller.lastError)
                        .foregroundStyle(.red)
                } else if !controller.lastInfo.isEmpty {
                    Text(controller.lastInfo)
                        .foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("当前策略").font(.title3.bold())
                    Text("模式：\(controller.settings.mode.title)")
                    Text("球种：\(controller.settings.gtypes)")
                    Text("发现间隔：\(controller.settings.discoverIntervalSeconds)s")
                    Text("监控循环：\(controller.settings.loopIntervalSeconds)s")
                    Text("分段时长：\(controller.settings.segmentMinutes) 分钟")
                    Text("整场时长：\(controller.settings.maxDurationMinutes == 0 ? "不限" : "\(controller.settings.maxDurationMinutes) 分钟")")
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("通知状态").font(.title3.bold())
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
                VStack(alignment: .leading, spacing: 8) {
                    Text("当前登录态来源").font(.title3.bold())
                    Text("UI 已经内嵌独立网页登录页。")
                    Text(controller.bridgeStatusSummary)
                        .foregroundStyle(controller.bridgePageState.hasLivePane && !controller.bridgePageState.loginRequired ? .green : .orange)
                    Text("当前页面：\(controller.bridgePageState.currentURL.isEmpty ? "未知" : controller.bridgePageState.currentURL)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("live 候选：\(controller.bridgePageState.liveCandidateCount) | loginRequired：\(controller.bridgePageState.loginRequired ? "是" : "否")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(controller.canLaunchRecorder ? "录制启动条件：已满足" : "录制启动条件：未满足")
                        .foregroundStyle(controller.canLaunchRecorder ? .green : .orange)
                    if !controller.canLaunchRecorder, !controller.launchGuardMessage.isEmpty {
                        Text(controller.launchGuardMessage)
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 24)
        }
    }

    func statusCard(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            Text(value).font(.system(size: 28, weight: .bold))
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
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
            return items.sorted { sort.ascending ? $0.startedAt < $1.startedAt : $0.startedAt > $1.startedAt }
        case .length:
            return items.sorted { sort.ascending ? workerDurationSeconds($0) < workerDurationSeconds($1) : workerDurationSeconds($0) > workerDurationSeconds($1) }
        }
    }

    private func workerDurationSeconds(_ w: WorkerStateSummary) -> TimeInterval {
        guard let start = parseISODateStatic(w.startedAt) else { return 0 }
        let end = parseISODateStatic(w.updatedAt) ?? Date()
        return end.timeIntervalSince(start)
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
