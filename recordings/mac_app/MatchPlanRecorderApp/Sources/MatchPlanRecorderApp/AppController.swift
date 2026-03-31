import Foundation
import Observation
import WebKit

@MainActor
@Observable
final class AppController {
    let projectRoot = "/Users/niannianshunjing/match_plan"
    let recordingsRoot = "/Users/niannianshunjing/match_plan/recordings"
    let supervisorScript = "/Users/niannianshunjing/match_plan/recordings/pion_gst_direct_chain/pion_gst_supervisor.py"
    let dispatcherRuntimeRoot = "/Users/niannianshunjing/match_plan/recordings/watch_runtime"
    let preferredPythonCandidates = [
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/bin/python3",
        "/usr/bin/python3",
    ]
    let preferredPathEntries = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]

    var settings = RecorderAppSettings()
    var supervisorStatus = SupervisorStatus()
    var workers: [WorkerStateSummary] = []
    var selectedWorkerID: String?
    var selectedHistoryWorkerID: String?
    var selectedHistoryWorkerIDs: Set<String> = []
    var logLines: [AppLogLine] = []
    var backendLogLines: [AppLogLine] = []
    var dispatcherLogLines: [AppLogLine] = []
    var selectedWorkerLogLines: [AppLogLine] = []
    var supervisorWrapperLogLines: [AppLogLine] = []
    var isBusy = false
    var lastError = ""
    var lastInfo = ""
    var pendingActionSummary = ""
    var pendingActionCommand = ""
    var appLoginIntegrationReady = false
    var bridgePageState = BridgePageState()
    var cleanupPreviewSummary = ""
    var cleanupPreviewDates: [String] = []
    var cleanupPreviewSamples: [String] = []
    var cleanupPreviewReady = false
    var artifacts: [ArtifactSessionSummary] = []
    var selectedArtifactIDs: Set<String> = []
    var currentDispatcherWorkerIDs: Set<String> = []

    let settingsURL: URL
    let diagnosticsDirURL: URL
    private var refreshTimer: Timer?

    init() {
        let support = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/MatchPlanRecorderApp", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        self.settingsURL = support.appendingPathComponent("settings.json")
        self.diagnosticsDirURL = support.appendingPathComponent("diagnostics", isDirectory: true)
        try? FileManager.default.createDirectory(at: diagnosticsDirURL, withIntermediateDirectories: true)
        loadSettings()
        appLoginIntegrationReady = AppWebBridge.shared.isReady
        Task {
            await refreshAll()
        }
        startAutoRefresh()
    }

    func loadSettings() {
        guard let data = try? Data(contentsOf: settingsURL),
              let decoded = try? JSONDecoder().decode(RecorderAppSettings.self, from: data)
        else { return }
        settings = decoded
        saveSettings()
    }

    func saveSettings() {
        guard let data = try? JSONEncoder().encode(settings) else { return }
        try? data.write(to: settingsURL, options: [.atomic])
    }

    func applySettings(_ newSettings: RecorderAppSettings, message: String = "配置已保存") {
        settings = newSettings
        saveSettings()
        lastError = ""
        lastInfo = message
        clearPendingAction()
        appendLog("[settings] \(message)")
    }

    func appendLog(_ text: String, source: String = "app") {
        logLines.insert(AppLogLine(text: text, source: source), at: 0)
        if logLines.count > 300 {
            logLines = Array(logLines.prefix(300))
        }
    }

    func startAutoRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                await self.refreshAll()
            }
        }
    }

    func buildSupervisorArgs(command: String) -> [String] {
        var args = [
            supervisorScript,
            command,
            "--job-id", settings.jobID,
            "--browser", settings.browser,
            "--gtypes", settings.gtypes,
            "--max-streams", String(settings.maxStreams),
            "--discover-interval-seconds", String(settings.discoverIntervalSeconds),
            "--loop-interval-seconds", String(settings.loopIntervalSeconds),
            "--segment-minutes", String(settings.segmentMinutes),
            "--max-duration-minutes", String(settings.maxDurationMinutes),
            "--archive-width", String(settings.archiveWidth),
            "--archive-height", String(settings.archiveHeight),
            "--archive-bitrate-kbps", String(settings.archiveBitrateKbps),
            "--hls-width", String(settings.hlsWidth),
            "--hls-height", String(settings.hlsHeight),
            "--hls-bitrate-kbps", String(settings.hlsBitrateKbps),
            "--chain-tag", settings.chainTag,
            "--runtime-dir", "\(dispatcherRuntimeRoot)/\(settings.chainTag)_dispatcher",
        ]
        if settings.skipDataBinding {
            args.append("--skip-data-binding")
        }
        if settings.allowUnbound {
            args.append("--allow-unbound")
        }
        if settings.notifications.pushToFeishu && !settings.notifications.target.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            args.append(contentsOf: [
                "--notify-channel", settings.notifications.channel,
                "--notify-account", settings.notifications.account,
                "--notify-target", settings.notifications.target,
            ])
            if settings.notifications.pushOnNewLive {
                args.append("--notify-on-new-live")
            }
            if settings.notifications.pushOnRecordingStarted {
                args.append("--notify-on-recording-started")
            }
            if settings.notifications.pushOnRecordingCompleted {
                args.append("--notify-on-recording-completed")
            }
            if settings.notifications.pushOnRecordingFailed {
                args.append("--notify-on-recording-failed")
            }
        }
        return args
    }

    func buildMaintenanceArgs(command: String, extra: [String] = []) -> [String] {
        [supervisorScript, command] + extra
    }

    func runPython(_ args: [String]) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonExecutablePath)
            process.arguments = args
            process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
            var env = ProcessInfo.processInfo.environment
            env["MATCH_PLAN_APP_WEB_BRIDGE_URL"] = bridgeBaseURL.absoluteString
            env["MATCH_PLAN_APP_WEB_BRIDGE_FALLBACK_TO_BROWSER"] = "0"
            env["PATH"] = normalizedPATH(from: env["PATH"])
            process.environment = env

            let tempURL = diagnosticsDirURL.appendingPathComponent("subprocess-\(UUID().uuidString).log")
            FileManager.default.createFile(atPath: tempURL.path, contents: nil)
            let outputHandle: FileHandle
            do {
                outputHandle = try FileHandle(forWritingTo: tempURL)
            } catch {
                continuation.resume(throwing: error)
                return
            }
            process.standardOutput = outputHandle
            process.standardError = outputHandle

            process.terminationHandler = { process in
                try? outputHandle.close()
                let data = (try? Data(contentsOf: tempURL)) ?? Data()
                try? FileManager.default.removeItem(at: tempURL)
                let output = String(data: data, encoding: .utf8) ?? ""
                if process.terminationStatus == 0 {
                    continuation.resume(returning: output)
                } else {
                    continuation.resume(throwing: NSError(domain: "MatchPlanRecorderApp", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: output.isEmpty ? "python command failed" : output
                    ]))
                }
            }

            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    var pythonExecutablePath: String {
        let fm = FileManager.default
        for candidate in preferredPythonCandidates where fm.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "/usr/bin/python3"
    }

    func normalizedPATH(from current: String?) -> String {
        var entries: [String] = preferredPathEntries
        if let current, !current.isEmpty {
            for item in current.split(separator: ":").map(String.init) where !entries.contains(item) {
                entries.append(item)
            }
        }
        return entries.joined(separator: ":")
    }

    func startSupervisor() async {
        await runCommand("start")
    }

    func stopSupervisor() async {
        await runCommand("stop")
    }

    func restartSupervisor() async {
        await runCommand("restart")
    }

    func ensureRunning() async {
        await runCommand("ensure-running")
    }

    func cleanupTestArtifacts() async {
        await runCommand("cleanup-test-artifacts")
    }

    func previewCleanupTestArtifacts() async {
        isBusy = true
        defer { isBusy = false }
        do {
            let output = try await runPython(buildSupervisorArgs(command: "preview-test-artifacts"))
            let data = Data(output.utf8)
            guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                lastError = "无法解析清理预览结果"
                appendLog("[preview-test-artifacts] ERROR: 无法解析预览结果")
                return
            }
            let sessionCount = payload["session_count"] as? Int ?? 0
            let runtimeCount = payload["runtime_count"] as? Int ?? 0
            let dateCounts = payload["date_counts"] as? [String: Int] ?? [:]
            let sampleSessions = payload["sample_sessions"] as? [String] ?? []
            cleanupPreviewSummary = "将删除测试 session \(sessionCount) 个，测试 runtime \(runtimeCount) 项。"
            cleanupPreviewDates = dateCounts.keys.sorted().map { "\($0)：\(dateCounts[$0] ?? 0) 个" }
            cleanupPreviewSamples = Array(sampleSessions.prefix(8))
            cleanupPreviewReady = sessionCount > 0 || runtimeCount > 0
            lastError = ""
            lastInfo = cleanupPreviewSummary
            appendLog("[preview-test-artifacts] \(cleanupPreviewSummary)")
        } catch {
            lastError = error.localizedDescription
            appendLog("[preview-test-artifacts] ERROR: \(error.localizedDescription)")
        }
    }

    func confirmCleanupTestArtifacts() async {
        await runCommand("cleanup-test-artifacts")
        cleanupPreviewSummary = ""
        cleanupPreviewDates = []
        cleanupPreviewSamples = []
        cleanupPreviewReady = false
    }

    func refreshArtifacts() async {
        do {
            let output = try await runPython(buildMaintenanceArgs(command: "list-artifacts"))
            writeDiagnostic(name: "list-artifacts.raw.txt", contents: output)
            let data = extractJSONObjectData(from: output) ?? Data(output.utf8)
            guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let sessions = payload["sessions"] as? [[String: Any]] else {
                lastError = "无法解析产物列表"
                appendLog("[list-artifacts] ERROR: 无法解析产物列表")
                return
            }
            let decoded = sessions.compactMap { item in
                artifactSummary(from: item)
            }
            artifacts = decoded
            let validIDs = Set(decoded.map(\.id))
            selectedArtifactIDs = selectedArtifactIDs.intersection(validIDs)
            lastError = ""
            lastInfo = "已加载 \(decoded.count) 个产物"
            appendLog("[list-artifacts] loaded \(decoded.count) artifacts")
        } catch {
            lastError = error.localizedDescription
            appendLog("[list-artifacts] ERROR: \(error.localizedDescription)")
        }
    }

    func artifactSummary(from item: [String: Any]) -> ArtifactSessionSummary? {
        guard let id = item["id"] as? String,
              let sessionDir = item["session_dir"] as? String,
              let sessionName = item["session_name"] as? String
        else {
            return nil
        }
        return ArtifactSessionSummary(
            id: id,
            session_dir: sessionDir,
            session_name: sessionName,
            mode: item["mode"] as? String ?? "",
            date: item["date"] as? String ?? "",
            match_dir_name: item["match_dir_name"] as? String ?? "",
            active: item["active"] as? Bool ?? false,
            active_state: item["active_state"] as? String ?? "",
            active_job_id: item["active_job_id"] as? String ?? "",
            title: item["title"] as? String ?? "",
            full_video_count: item["full_video_count"] as? Int ?? 0,
            segment_count: item["segment_count"] as? Int ?? 0,
            has_hls: item["has_hls"] as? Bool ?? false,
            can_delete_directly: item["can_delete_directly"] as? Bool ?? false
        )
    }

    func deleteSelectedArtifacts(stopActive: Bool) async {
        let selected = selectedArtifacts
        guard !selected.isEmpty else {
            lastInfo = "请先选择要删除的产物"
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            var extra: [String] = []
            for item in selected {
                extra.append("--session")
                extra.append(item.session_dir)
            }
            if stopActive {
                extra.append("--stop-active")
            }
            pendingActionCommand = stopActive ? "delete-artifacts-stop-active" : "delete-artifacts"
            pendingActionSummary = stopActive ? "已发送停止后删除命令，正在停止对应任务并删除所选产物..." : "已发送删除命令，正在删除所选已结束产物..."
            lastInfo = pendingActionSummary
            appendLog("[delete-artifacts] \(pendingActionSummary)")
            let output = try await runPython(buildMaintenanceArgs(command: "delete-artifacts", extra: extra))
            appendLog("[delete-artifacts] \(output.trimmingCharacters(in: .whitespacesAndNewlines))")
            selectedArtifactIDs.removeAll()
            await refreshArtifacts()
            await refreshAll()
            lastError = ""
            lastInfo = stopActive ? "已停止对应任务并删除所选产物" : "已删除所选产物"
            clearPendingAction()
        } catch {
            lastError = error.localizedDescription
            clearPendingAction()
            appendLog("[delete-artifacts] ERROR: \(error.localizedDescription)")
        }
    }

    func runCommand(_ command: String) async {
        isBusy = true
        defer { isBusy = false }
        if ["start", "restart", "ensure-running"].contains(command), !canLaunchRecorder {
            let message = launchGuardMessage
            lastError = message
            appendLog("[\(command)] BLOCKED: \(message)")
            return
        }
        do {
            saveSettings()
            pendingActionCommand = command
            pendingActionSummary = commandPendingSummary(command)
            lastInfo = pendingActionSummary
            appendLog("[\(command)] \(pendingActionSummary)")
            let output = try await runPython(buildSupervisorArgs(command: command))
            appendLog("[\(command)] \(output.trimmingCharacters(in: .whitespacesAndNewlines))")
            lastError = ""
            lastInfo = commandCompletedSummary(command)
            await refreshAll()
            if ["stop", "cleanup-test-artifacts"].contains(command) {
                clearPendingAction()
            }
        } catch {
            lastError = error.localizedDescription
            clearPendingAction()
            appendLog("[\(command)] ERROR: \(error.localizedDescription)")
        }
    }

    func refreshAll() async {
        ensureDataSiteProxy()
        appLoginIntegrationReady = AppWebBridge.shared.isReady
        await refreshBridgePageState()
        if shouldAutoHealBridge {
            AppWebBridge.shared.ensureSchedulesLiveLoaded()
        }
        await refreshSupervisorStatus()
        refreshWorkerStatuses()
        refreshBackendLogs()
        await refreshArtifacts()
        if pendingActionCommand.isEmpty || shouldClearPendingAction {
            isBusy = false
        }
        if shouldClearPendingAction {
            if pendingActionCommand == "stop" {
                lastInfo = "录制任务已停止"
            } else if pendingActionCommand == "cleanup-test-artifacts" {
                lastInfo = "测试产物已清理完成"
            }
            clearPendingAction()
        }
    }

    func clearPendingAction() {
        pendingActionSummary = ""
        pendingActionCommand = ""
    }

    var hasActiveRuntime: Bool {
        supervisorStatus.dispatcher_alive || supervisorStatus.alive_worker_count > 0 || !workers.isEmpty
    }

    var controlsLocked: Bool {
        if pendingActionCommand.isEmpty {
            return false
        }
        return !shouldClearPendingAction
    }

    var shouldClearPendingAction: Bool {
        guard !pendingActionCommand.isEmpty else { return false }
        switch pendingActionCommand {
        case "stop", "cleanup-test-artifacts":
            return !hasActiveRuntime || supervisorStatus.state == "stopped" || supervisorStatus.state == "missing"
        case "start", "restart", "ensure-running":
            return supervisorStatus.dispatcher_alive || hasActiveRuntime || supervisorStatus.state == "running"
        default:
            return false
        }
    }

    var shouldAutoHealBridge: Bool {
        guard appLoginIntegrationReady else { return false }
        if bridgePageState.currentURL.isEmpty {
            return true
        }
        if bridgePageState.currentURL == "about:blank" {
            return true
        }
        if bridgePageState.ok && bridgePageState.loginRequired {
            return false
        }
        if bridgePageState.ok && bridgePageState.hasLivePane {
            return false
        }
        return true
    }

    func refreshBridgePageState() async {
        guard let url = URL(string: "\(bridgeBaseURL.absoluteString)/page-state") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            if let decoded = try? JSONDecoder().decode(BridgePageState.self, from: data) {
                bridgePageState = decoded
            } else {
                bridgePageState = BridgePageState(ok: false, webViewReady: appLoginIntegrationReady, error: "无法解析 bridge 页面状态")
            }
        } catch {
            bridgePageState = BridgePageState(ok: false, webViewReady: appLoginIntegrationReady, error: error.localizedDescription)
        }
    }

    func refreshSupervisorStatus() async {
        do {
            let output = try await runPython(buildSupervisorArgs(command: "status"))
            let data = Data(output.utf8)
            if let decoded = try? JSONDecoder().decode(SupervisorStatus.self, from: data) {
                supervisorStatus = decoded
                lastError = ""
            } else {
                if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   String(describing: payload["state"] ?? "") == "missing" {
                    supervisorStatus = SupervisorStatus(job_id: settings.jobID, state: "stopped")
                    lastError = ""
                } else {
                    lastError = "无法解析 supervisor 状态"
                }
            }
        } catch {
            lastError = error.localizedDescription
            appendLog("[status] ERROR: \(error.localizedDescription)")
        }
    }

    func refreshWorkerStatuses() {
        let dispatcherStateURL = URL(fileURLWithPath: "\(dispatcherRuntimeRoot)/\(settings.chainTag)_dispatcher/dispatcher_state.json")
        if let data = try? Data(contentsOf: dispatcherStateURL),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let workerItems = payload["workers"] as? [[String: Any]] {
            currentDispatcherWorkerIDs = Set(workerItems.compactMap { item in
                guard let path = item["status_path"] as? String, !path.isEmpty else { return nil }
                return URL(fileURLWithPath: path).lastPathComponent
            })
        } else {
            currentDispatcherWorkerIDs = []
        }

        let workerDir = URL(fileURLWithPath: "\(dispatcherRuntimeRoot)/\(settings.chainTag)_dispatcher/worker_status", isDirectory: true)
        guard let items = try? FileManager.default.contentsOfDirectory(at: workerDir, includingPropertiesForKeys: nil) else {
            workers = []
            selectedWorkerID = nil
            selectedHistoryWorkerID = nil
            return
        }
        let summaries: [WorkerStateSummary] = items.compactMap { url in
            guard let data = try? Data(contentsOf: url),
                  let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return nil }
            let title = (payload["teams"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                ?? (payload["matchId"] as? String)
                ?? url.deletingPathExtension().lastPathComponent
            var stateValue = payload["state"] as? String ?? "unknown"
            var stopReasonValue = payload["stopReason"] as? String ?? ""
            if !supervisorStatus.dispatcher_alive,
               stateValue == "stopping",
               (stopReasonValue == "manual_stop" || stopReasonValue.hasPrefix("signal_")) {
                stateValue = "stopped"
                stopReasonValue = "manual_stop"
            }
            let sessionDir = payload["sessionDir"] as? String ?? ""
            let previewURL = Self.findPreviewURL(sessionDir: sessionDir)
            let mergedVideoURL = Self.findMergedVideoURL(sessionDir: sessionDir)
            let dataFileURL = Self.findDataFileURL(sessionDir: sessionDir)
            let internalStatus = Self.loadInternalRecorderStatus(sessionDir: sessionDir)
            return WorkerStateSummary(
                id: url.lastPathComponent,
                title: title,
                state: stateValue,
                stopReason: stopReasonValue,
                sessionDir: sessionDir,
                league: payload["league"] as? String ?? "",
                teamH: payload["team_h"] as? String ?? "",
                teamC: payload["team_c"] as? String ?? "",
                gid: payload["gid"] as? String ?? "",
                ecid: payload["ecid"] as? String ?? "",
                hgid: payload["hgid"] as? String ?? "",
                updatedAt: payload["updatedAt"] as? String ?? "",
                previewURL: previewURL,
                mergedVideoURL: mergedVideoURL,
                dataFileURL: dataFileURL,
                matchedRows: payload["matchedRows"] as? Int ?? 0,
                note: payload["recordingNote"] as? String ?? "",
                serverHost: payload["serverHost"] as? String ?? "",
                connected: internalStatus["connected"] as? Bool ?? false,
                activeSegments: internalStatus["segmentCount"] as? Int ?? 0,
                hlsSegmentCount: internalStatus["hlsSegmentCount"] as? Int ?? 0,
                videoCodec: internalStatus["videoCodec"] as? String ?? "",
                audioCodec: internalStatus["audioCodec"] as? String ?? "",
                effectiveFPS: internalStatus["archiveEffectiveFps"] as? Double ?? 0,
                lowFrameRate: internalStatus["lowFrameRate"] as? Bool ?? false,
                lastError: (payload["error"] as? String ?? "").isEmpty ? (internalStatus["lastError"] as? String ?? "") : (payload["error"] as? String ?? ""),
                lastPacketAt: internalStatus["lastPacketAt"] as? String ?? "",
                startedAt: internalStatus["startedAt"] as? String ?? (payload["startedAt"] as? String ?? "")
            )
        }
        workers = summaries.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        if selectedWorkerID == nil || !activeWorkers.contains(where: { $0.id == selectedWorkerID }) {
            selectedWorkerID = activeWorkers.first?.id
        }
        if selectedHistoryWorkerID == nil || !historicalWorkers.contains(where: { $0.id == selectedHistoryWorkerID }) {
            selectedHistoryWorkerID = historicalWorkers.first?.id
        }
        let validHistoricalIDs = Set(historicalWorkers.map(\.id))
        selectedHistoryWorkerIDs = selectedHistoryWorkerIDs.intersection(validHistoricalIDs)
    }

    func refreshBackendLogs() {
        let runtimeDispatcherURL = URL(fileURLWithPath: "\(dispatcherRuntimeRoot)/\(settings.chainTag)_dispatcher/dispatcher.log")
        dispatcherLogLines = readTailLogLines(at: runtimeDispatcherURL, source: "dispatcher", limit: 260)

        let wrapperPath = supervisorStatus.dispatcher_log.trimmingCharacters(in: .whitespacesAndNewlines)
        if !wrapperPath.isEmpty {
            supervisorWrapperLogLines = readTailLogLines(at: URL(fileURLWithPath: wrapperPath), source: "supervisor", limit: 120)
        } else {
            supervisorWrapperLogLines = []
        }

        if let worker = selectedWorker ?? activeWorkers.first,
           let recordingLogURL = Self.findRecordingLogURL(sessionDir: worker.sessionDir) {
            selectedWorkerLogLines = readTailLogLines(at: recordingLogURL, source: "worker", limit: 160)
        } else {
            selectedWorkerLogLines = []
        }

        backendLogLines = dispatcherLogLines + selectedWorkerLogLines + supervisorWrapperLogLines
    }

    func readTailLogLines(at url: URL, source: String, limit: Int) -> [AppLogLine] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return []
        }
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
            .suffix(limit)
            .map { String($0) }
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return lines.reversed().map { AppLogLine(text: $0, source: source) }
    }

    var schedulesURL: URL {
        URL(string: "https://sftraders.live/schedules/live")!
    }

    let dataSiteProxyScript = "/Users/niannianshunjing/match_plan/recordings/mac_app/MatchPlanRecorderApp/data_site_proxy.py"
    private var dataSiteProxyProcess: Process?

    var dataEntryURL: URL {
        URL(string: "http://127.0.0.1:18780")!
    }

    var dataSiteProxyReady = false

    func ensureDataSiteProxy() {
        // Already confirmed ready
        if dataSiteProxyReady { return }

        // Try to start proxy process if not running
        if dataSiteProxyProcess == nil || !(dataSiteProxyProcess?.isRunning ?? false) {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: pythonExecutablePath)
            proc.arguments = [dataSiteProxyScript]
            let logURL = diagnosticsDirURL.appendingPathComponent("data_site_proxy.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
            let logHandle = try? FileHandle(forWritingTo: logURL)
            proc.standardOutput = logHandle ?? FileHandle.nullDevice
            proc.standardError = logHandle ?? FileHandle.nullDevice
            do {
                try proc.run()
                dataSiteProxyProcess = proc
                appendLog("数据站本地代理启动中...")
            } catch {
                appendLog("数据站本地代理进程启动失败: \(error.localizedDescription)")
            }
        }

        // Check /ping inline (called every 5s from refreshAll)
        checkDataSiteProxyPing()
    }

    private func checkDataSiteProxyPing() {
        guard !dataSiteProxyReady else { return }
        guard let url = URL(string: "http://127.0.0.1:18780/ping") else { return }
        let req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)
        URLSession.shared.dataTask(with: req) { [weak self] data, resp, _ in
            guard let data,
                  (resp as? HTTPURLResponse)?.statusCode == 200,
                  String(data: data, encoding: .utf8) == "ok" else { return }
            DispatchQueue.main.async {
                guard let self, !self.dataSiteProxyReady else { return }
                self.dataSiteProxyReady = true
                self.appendLog("数据站本地代理就绪 (port 18780)")
            }
        }.resume()
    }

    var bridgeBaseURL: URL {
        AppWebBridge.shared.baseURL
    }

    func registerAppWebView(_ webView: WKWebView, preferred: Bool) {
        AppWebBridge.shared.register(webView: webView, preferred: preferred)
        appLoginIntegrationReady = AppWebBridge.shared.isReady
        setBridgeEnvironmentForBackend()
        appendLog("App 内嵌网页登录页已注册(\(preferred ? "primary" : "fallback"))，本地 bridge: \(bridgeBaseURL.absoluteString)")
    }

    func refreshEmbeddedSession() async {
        AppWebBridge.shared.reloadRegisteredWebViews()
        appendLog("已请求刷新内嵌网页登录页")
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        await refreshAll()
    }

    func clearWebsiteData() async {
        let dataStore = WKWebsiteDataStore.default()
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        let records = await dataStore.dataRecords(ofTypes: types)
        await dataStore.removeData(ofTypes: types, for: records)
        appendLog("已清除所有网站缓存和 Cookie（共 \(records.count) 条记录）")
    }

    func setBridgeEnvironmentForBackend() {
        setenv("MATCH_PLAN_APP_WEB_BRIDGE_URL", bridgeBaseURL.absoluteString, 1)
    }

    var selectedWorker: WorkerStateSummary? {
        guard let selectedWorkerID else { return activeWorkers.first }
        return activeWorkers.first(where: { $0.id == selectedWorkerID }) ?? activeWorkers.first
    }

    var selectedHistoryWorker: WorkerStateSummary? {
        guard let selectedHistoryWorkerID else { return historicalWorkers.first }
        return historicalWorkers.first(where: { $0.id == selectedHistoryWorkerID }) ?? historicalWorkers.first
    }

    var selectedHistoricalWorkers: [WorkerStateSummary] {
        historicalWorkers.filter { selectedHistoryWorkerIDs.contains($0.id) }
    }

    var selectedHistoricalSessionDirs: [String] {
        Array(Set(selectedHistoricalWorkers.map(\.sessionDir).filter { !$0.isEmpty })).sorted()
    }

    var selectedHistoricalArtifacts: [ArtifactSessionSummary] {
        let selectedDirs = Set(selectedHistoricalSessionDirs)
        return artifacts.filter { selectedDirs.contains($0.session_dir) }
    }

    var canDeleteSelectedHistory: Bool {
        !selectedHistoricalSessionDirs.isEmpty && !controlsLocked
    }

    func deleteSelectedHistoryArtifacts() async {
        let selectedDirs = selectedHistoricalSessionDirs
        guard !selectedDirs.isEmpty else {
            lastInfo = "请先选择要清理的历史记录"
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            var extra: [String] = []
            for sessionDir in selectedDirs {
                extra.append("--session")
                extra.append(sessionDir)
            }
            pendingActionCommand = "delete-artifacts"
            pendingActionSummary = "已发送删除命令，正在删除所选历史记录对应的产物..."
            lastInfo = pendingActionSummary
            appendLog("[delete-history] \(pendingActionSummary)")
            let output = try await runPython(buildMaintenanceArgs(command: "delete-artifacts", extra: extra))
            appendLog("[delete-history] \(output.trimmingCharacters(in: .whitespacesAndNewlines))")
            selectedHistoryWorkerIDs.removeAll()
            selectedHistoryWorkerID = nil
            await refreshArtifacts()
            await refreshAll()
            lastError = ""
            lastInfo = "已删除所选历史记录对应的产物"
            clearPendingAction()
        } catch {
            lastError = error.localizedDescription
            clearPendingAction()
            appendLog("[delete-history] ERROR: \(error.localizedDescription)")
        }
    }

    var failedWorkers: [WorkerStateSummary] {
        workers.filter {
            $0.state == "failed" || $0.state == "skipped" || !$0.failureSummary.isEmpty
        }
    }

    var activeWorkers: [WorkerStateSummary] {
        if !currentDispatcherWorkerIDs.isEmpty {
            return workers.filter { currentDispatcherWorkerIDs.contains($0.id) }
        }
        return workers.filter {
            switch $0.state {
            case "completed", "failed", "skipped", "stopped":
                return false
            default:
                return true
            }
        }
    }

    var historicalWorkers: [WorkerStateSummary] {
        let activeIDs = Set(activeWorkers.map(\.id))
        return workers.filter { !activeIDs.contains($0.id) }
    }

    var runtimePhaseTitle: String {
        if supervisorStatus.recording_worker_count > 0 {
            return failedWorkers.isEmpty ? "录制中" : "录制中（部分异常）"
        }
        if supervisorStatus.alive_worker_count > 0 || supervisorStatus.dispatcher_alive {
            return "监听中"
        }
        if !appLoginIntegrationReady {
            return "Bridge 未就绪"
        }
        if !bridgeSessionReady {
            return "等待登录"
        }
        return "已停止"
    }

    var runtimePhaseDetail: String {
        if supervisorStatus.recording_worker_count > 0 {
            return "当前有 \(supervisorStatus.recording_worker_count) 条 worker 正在录制。"
        }
        if supervisorStatus.alive_worker_count > 0 {
            return "当前有 \(supervisorStatus.alive_worker_count) 条 worker 正在建连或等待轨道。"
        }
        if supervisorStatus.dispatcher_alive {
            return "录制链正在运行，但当前没有活跃 worker。"
        }
        if !appLoginIntegrationReady {
            return "App 内嵌会话还没有完成 bridge 注册。"
        }
        if !bridgeSessionReady {
            return launchGuardMessage
        }
        return "录制链当前没有运行，点击启动或确保运行即可。"
    }

    var startupProgressLines: [String] {
        var lines: [String] = []
        let shouldPreferRuntimeState = supervisorStatus.dispatcher_alive || !workers.isEmpty || supervisorStatus.alive_worker_count > 0
        if shouldShowPendingAction && !shouldPreferRuntimeState {
            lines.append(pendingActionSummary)
        }
        if supervisorStatus.dispatcher_alive {
            lines.append("已启动 dispatcher")
        }
        if supervisorStatus.dispatcher_alive && workers.isEmpty {
            lines.append("正在发现比赛")
        }
        if !workers.isEmpty {
            lines.append("已分发 \(workers.count) 条 worker")
        }
        if supervisorStatus.recording_worker_count > 0 {
            lines.append("当前录制 \(supervisorStatus.recording_worker_count) 条")
        } else if supervisorStatus.alive_worker_count > 0 {
            lines.append("当前活跃 \(supervisorStatus.alive_worker_count) 条")
        }
        if lines.isEmpty {
            if bridgeSessionReady {
                lines.append("等待你点击启动")
            } else {
                lines.append("等待登录完成")
            }
        }
        return lines
    }

    var shouldShowPendingAction: Bool {
        guard !pendingActionSummary.isEmpty else { return false }
        switch pendingActionCommand {
        case "stop", "cleanup-test-artifacts", "delete-artifacts", "delete-artifacts-stop-active":
            return hasActiveRuntime || isBusy
        case "start", "restart", "ensure-running":
            return isBusy && !supervisorStatus.dispatcher_alive && workers.isEmpty && supervisorStatus.alive_worker_count == 0
        default:
            return isBusy
        }
    }

    func commandPendingSummary(_ command: String) -> String {
        switch command {
        case "start":
            return "已发送启动命令，正在拉起 dispatcher..."
        case "restart":
            return "已发送重启命令，正在切换到新配置..."
        case "ensure-running":
            return "已发送保活命令，正在检查录制链..."
        case "stop":
            return "已发送停止命令，正在并行停止 worker 和 dispatcher..."
        case "cleanup-test-artifacts":
            return "已发送清理命令，正在停止测试链并删除测试产物..."
        case "delete-artifacts":
            return "已发送删除命令，正在删除所选已结束产物..."
        case "delete-artifacts-stop-active":
            return "已发送停止后删除命令，正在停止对应任务并删除所选产物..."
        default:
            return "\(command) 命令已发送"
        }
    }

    func commandCompletedSummary(_ command: String) -> String {
        switch command {
        case "start":
            return "启动命令已执行"
        case "restart":
            return "重启命令已执行"
        case "ensure-running":
            return "保活检查已执行"
        case "stop":
            return "停止命令已执行"
        case "cleanup-test-artifacts":
            return "测试产物清理已执行"
        case "delete-artifacts":
            return "删除命令已执行"
        case "delete-artifacts-stop-active":
            return "停止后删除命令已执行"
        default:
            return "\(command) 已执行"
        }
    }

    var stageCounts: [(String, Int)] {
        let grouped = Dictionary(grouping: workers, by: { $0.stageTitle })
        return grouped.map { ($0.key, $0.value.count) }
            .sorted { lhs, rhs in
                if lhs.1 == rhs.1 {
                    return lhs.0 < rhs.0
                }
                return lhs.1 > rhs.1
            }
    }

    var bridgeSessionReady: Bool {
        bridgePageState.ok && bridgePageState.webViewReady && bridgePageState.hasLivePane && !bridgePageState.loginRequired
    }

    var canLaunchRecorder: Bool {
        bridgeSessionReady
    }

    var bridgeStatusSummary: String {
        if bridgePageState.hasLivePane && !bridgePageState.loginRequired {
            if bridgePageState.liveCandidateCount > 0 {
                return "当前后端已可直接使用 App 内会话，已拿到 \(bridgePageState.liveCandidateCount) 场 live。"
            }
            return "当前后端已可直接使用 App 内会话，live 列表正常，但当前没有直播。"
        }
        return "当前 App bridge 已启动，但还需要在 App 内页完成登录或进入 live 列表。"
    }

    var launchGuardMessage: String {
        if !appLoginIntegrationReady {
            return "App bridge 还没准备好，请先打开 App 内登录页。"
        }
        if bridgePageState.loginRequired {
            return "请先在 App 内登录 SF Traders，再启动录制。"
        }
        if !bridgePageState.hasLivePane {
            return "App 内页还没进入 schedules/live，暂时不能启动录制。"
        }
        if !bridgePageState.ok {
            return bridgePageState.error.isEmpty ? "App bridge 状态未就绪。" : bridgePageState.error
        }
        return ""
    }

    var selectedArtifacts: [ArtifactSessionSummary] {
        artifacts.filter { selectedArtifactIDs.contains($0.id) }
    }

    var selectedActiveArtifacts: [ArtifactSessionSummary] {
        selectedArtifacts.filter(\.active)
    }

    var selectedEndedArtifacts: [ArtifactSessionSummary] {
        selectedArtifacts.filter { !$0.active }
    }

    var canDeleteSelectedDirectly: Bool {
        !selectedArtifacts.isEmpty && selectedActiveArtifacts.isEmpty && !controlsLocked
    }

    var canStopAndDeleteSelected: Bool {
        !selectedArtifacts.isEmpty && !selectedActiveArtifacts.isEmpty && !controlsLocked
    }

    func elapsedSeconds(from value: String) -> Int {
        let raw = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let date = parseISODate(raw) else { return 0 }
        return max(0, Int(Date().timeIntervalSince(date)))
    }

    func elapsedSeconds(from startValue: String, to endValue: String?) -> Int {
        let startRaw = startValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let start = parseISODate(startRaw) else { return 0 }
        if let endValue {
            let endRaw = endValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if let end = parseISODate(endRaw) {
                return max(0, Int(end.timeIntervalSince(start)))
            }
        }
        return max(0, Int(Date().timeIntervalSince(start)))
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

    func extractJSONObjectData(from raw: String) -> Data? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if let direct = text.data(using: .utf8),
           (try? JSONSerialization.jsonObject(with: direct)) != nil {
            return direct
        }
        guard let start = text.firstIndex(of: "{"),
              let end = text.lastIndex(of: "}") else {
            return nil
        }
        let slice = String(text[start...end])
        guard let data = slice.data(using: .utf8),
              (try? JSONSerialization.jsonObject(with: data)) != nil else {
            return nil
        }
        return data
    }

    func writeDiagnostic(name: String, contents: String) {
        let url = diagnosticsDirURL.appendingPathComponent(name)
        try? contents.write(to: url, atomically: true, encoding: .utf8)
    }

    func formatDuration(_ total: Int) -> String {
        let seconds = max(0, total)
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        let s = seconds % 60
        if h > 0 {
            return String(format: "%02d:%02d:%02d", h, m, s)
        }
        return String(format: "%02d:%02d", m, s)
    }

    var totalActiveDurationText: String {
        formatDuration(activeWorkers.reduce(0) { $0 + elapsedSeconds(from: $1.startedAt, to: nil) })
    }

    var maxActiveDurationText: String {
        formatDuration(activeWorkers.map { elapsedSeconds(from: $0.startedAt, to: nil) }.max() ?? 0)
    }

    func workerHasTerminalState(_ item: WorkerStateSummary) -> Bool {
        switch item.state {
        case "completed", "failed", "skipped", "stopped":
            return true
        default:
            return false
        }
    }

    func workerDurationText(_ item: WorkerStateSummary) -> String {
        let endValue = workerHasTerminalState(item) ? item.updatedAt : nil
        return formatDuration(elapsedSeconds(from: item.startedAt, to: endValue))
    }

    static func findPreviewURL(sessionDir: String) -> URL? {
        guard !sessionDir.isEmpty else { return nil }
        let base = URL(fileURLWithPath: sessionDir, isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: base, includingPropertiesForKeys: nil) else {
            return nil
        }
        for case let url as URL in enumerator {
            if url.lastPathComponent == "playlist.m3u8" && url.path.contains("/hls/") {
                return url
            }
        }
        return nil
    }

    static func findMergedVideoURL(sessionDir: String) -> URL? {
        guard !sessionDir.isEmpty else { return nil }
        let base = URL(fileURLWithPath: sessionDir, isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: base, includingPropertiesForKeys: nil) else {
            return nil
        }
        for case let url as URL in enumerator {
            if url.pathExtension.lowercased() == "mp4", url.lastPathComponent.contains("__full") {
                return url
            }
        }
        return nil
    }

    static func findDataFileURL(sessionDir: String) -> URL? {
        guard !sessionDir.isEmpty else { return nil }
        let base = URL(fileURLWithPath: sessionDir, isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: base, includingPropertiesForKeys: nil) else {
            return nil
        }
        for case let url as URL in enumerator {
            if url.lastPathComponent.hasSuffix("__betting_data.jsonl") {
                return url
            }
        }
        return nil
    }

    static func findRecordingLogURL(sessionDir: String) -> URL? {
        guard !sessionDir.isEmpty else { return nil }
        let base = URL(fileURLWithPath: sessionDir, isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: base, includingPropertiesForKeys: nil) else {
            return nil
        }
        for case let url as URL in enumerator {
            if url.lastPathComponent == "recording.log" {
                return url
            }
        }
        return nil
    }

    static func loadInternalRecorderStatus(sessionDir: String) -> [String: Any] {
        guard !sessionDir.isEmpty else { return [:] }
        let base = URL(fileURLWithPath: sessionDir, isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: base, includingPropertiesForKeys: nil) else {
            return [:]
        }
        for case let url as URL in enumerator {
            if url.lastPathComponent == "pion_gst_status.json",
               let data = try? Data(contentsOf: url),
               let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return payload
            }
        }
        return [:]
    }
}
