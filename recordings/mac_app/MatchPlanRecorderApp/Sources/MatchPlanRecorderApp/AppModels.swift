import Foundation

struct RecorderNotificationSettings: Codable, Equatable {
    var pushToFeishu: Bool = false
    var channel: String = "feishu"
    var account: String = "legacy"
    var target: String = "oc_d8caa357cf6943f7a0b2917a2488876a"
    var pushOnNewLive: Bool = true
    var pushOnRecordingStarted: Bool = true
    var pushOnRecordingCompleted: Bool = true
    var pushOnRecordingFailed: Bool = true

    enum CodingKeys: String, CodingKey {
        case pushToFeishu
        case channel
        case account
        case target
        case pushOnNewLive
        case pushOnRecordingStarted
        case pushOnRecordingCompleted
        case pushOnRecordingFailed
    }

    init() {}

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        pushToFeishu = try container.decodeIfPresent(Bool.self, forKey: .pushToFeishu) ?? false
        channel = try container.decodeIfPresent(String.self, forKey: .channel) ?? "feishu"
        account = try container.decodeIfPresent(String.self, forKey: .account) ?? "legacy"
        target = try container.decodeIfPresent(String.self, forKey: .target) ?? "oc_d8caa357cf6943f7a0b2917a2488876a"
        pushOnNewLive = try container.decodeIfPresent(Bool.self, forKey: .pushOnNewLive) ?? true
        pushOnRecordingStarted = try container.decodeIfPresent(Bool.self, forKey: .pushOnRecordingStarted) ?? true
        pushOnRecordingCompleted = try container.decodeIfPresent(Bool.self, forKey: .pushOnRecordingCompleted) ?? true
        pushOnRecordingFailed = try container.decodeIfPresent(Bool.self, forKey: .pushOnRecordingFailed) ?? true
    }
}

enum RecorderMode: String, Codable, CaseIterable, Identifiable {
    case formalBoundOnly
    case bestEffortAll

    var id: String { rawValue }

    var title: String {
        switch self {
        case .formalBoundOnly:
            "正式录制"
        case .bestEffortAll:
            "稳定性测试"
        }
    }

    var description: String {
        switch self {
        case .formalBoundOnly:
            "只录你当前选择的球种，并要求匹配到数据"
        case .bestEffortAll:
            "按你当前选择的球种录制，不强制匹配数据，先验证接流稳定性"
        }
    }
}

struct RecorderAppSettings: Codable, Equatable {
    var browser: String = "app"
    var gtypes: String = "FT"
    var mode: RecorderMode = .formalBoundOnly
    var discoverIntervalSeconds: Int = 900
    var loopIntervalSeconds: Int = 1
    var segmentMinutes: Int = 5
    var maxDurationMinutes: Int = 0
    var archiveWidth: Int = 960
    var archiveHeight: Int = 540
    var archiveBitrateKbps: Int = 5000
    var hlsWidth: Int = 960
    var hlsHeight: Int = 540
    var hlsBitrateKbps: Int = 3500
    var maxStreams: Int = 0
    var prestartMinutes: Int = 1
    var keepRunning: Bool = true
    var previewEnabled: Bool = true
    var notifications: RecorderNotificationSettings = .init()

    enum CodingKeys: String, CodingKey {
        case browser
        case gtypes
        case mode
        case discoverIntervalSeconds
        case loopIntervalSeconds
        case segmentMinutes
        case maxDurationMinutes
        case archiveWidth
        case archiveHeight
        case archiveBitrateKbps
        case hlsWidth
        case hlsHeight
        case hlsBitrateKbps
        case maxStreams
        case prestartMinutes
        case keepRunning
        case previewEnabled
        case notifications
    }

    init() {}

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        browser = try container.decodeIfPresent(String.self, forKey: .browser) ?? "app"
        if browser == "safari" {
            browser = "app"
        }
        gtypes = try container.decodeIfPresent(String.self, forKey: .gtypes) ?? "FT"
        mode = try container.decodeIfPresent(RecorderMode.self, forKey: .mode) ?? .formalBoundOnly
        discoverIntervalSeconds = try container.decodeIfPresent(Int.self, forKey: .discoverIntervalSeconds) ?? 900
        loopIntervalSeconds = try container.decodeIfPresent(Int.self, forKey: .loopIntervalSeconds) ?? 1
        segmentMinutes = try container.decodeIfPresent(Int.self, forKey: .segmentMinutes) ?? 5
        maxDurationMinutes = try container.decodeIfPresent(Int.self, forKey: .maxDurationMinutes) ?? 0
        archiveWidth = try container.decodeIfPresent(Int.self, forKey: .archiveWidth) ?? 960
        archiveHeight = try container.decodeIfPresent(Int.self, forKey: .archiveHeight) ?? 540
        archiveBitrateKbps = try container.decodeIfPresent(Int.self, forKey: .archiveBitrateKbps) ?? 5000
        hlsWidth = try container.decodeIfPresent(Int.self, forKey: .hlsWidth) ?? 960
        hlsHeight = try container.decodeIfPresent(Int.self, forKey: .hlsHeight) ?? 540
        hlsBitrateKbps = try container.decodeIfPresent(Int.self, forKey: .hlsBitrateKbps) ?? 3500
        maxStreams = try container.decodeIfPresent(Int.self, forKey: .maxStreams) ?? 0
        prestartMinutes = try container.decodeIfPresent(Int.self, forKey: .prestartMinutes) ?? 1
        keepRunning = try container.decodeIfPresent(Bool.self, forKey: .keepRunning) ?? true
        previewEnabled = try container.decodeIfPresent(Bool.self, forKey: .previewEnabled) ?? true
        notifications = try container.decodeIfPresent(RecorderNotificationSettings.self, forKey: .notifications) ?? .init()
    }

    var skipDataBinding: Bool {
        mode == .bestEffortAll
    }

    var allowUnbound: Bool {
        mode == .bestEffortAll
    }

    var chainTag: String {
        switch mode {
        case .formalBoundOnly:
            "pgstapp"
        case .bestEffortAll:
            "pgstapp_test"
        }
    }

    var jobID: String {
        switch mode {
        case .formalBoundOnly:
            "pion_gst_mac_app_formal"
        case .bestEffortAll:
            "pion_gst_mac_app_test"
        }
    }
}

struct SupervisorStatus: Codable, Equatable {
    var job_id: String = ""
    var state: String = "unknown"
    var dispatcher_pid: Int = 0
    var dispatcher_alive: Bool = false
    var browser: String = ""
    var gtypes: String = ""
    var max_streams: Int = 0
    var discover_interval_seconds: Int = 0
    var loop_interval_seconds: Int = 0
    var segment_minutes: Int = 0
    var max_duration_minutes: Int = 0
    var archive_width: Int = 960
    var archive_height: Int = 540
    var archive_bitrate_kbps: Int = 5000
    var hls_width: Int = 960
    var hls_height: Int = 540
    var hls_bitrate_kbps: Int = 3500
    var skip_data_binding: Bool = false
    var allow_unbound: Bool = false
    var chain_tag: String = ""
    var notify_channel: String = ""
    var notify_account: String = ""
    var notify_target: String = ""
    var notify_on_new_live: Bool = false
    var notify_on_recording_started: Bool = false
    var notify_on_recording_completed: Bool = false
    var notify_on_recording_failed: Bool = false
    var dispatcher_log: String = ""
    var started_at: String = ""
    var updated_at: String = ""
    var stopped_at: String = ""
    var worker_count: Int = 0
    var alive_worker_count: Int = 0
    var recording_worker_count: Int = 0
    var recent_finished_count: Int = 0

    var notificationsEnabled: Bool {
        !notify_channel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
        !notify_target.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

struct WorkerStateSummary: Identifiable, Hashable {
    let id: String
    let title: String
    let state: String
    let stopReason: String
    let sessionDir: String
    let league: String
    let teamH: String
    let teamC: String
    let gid: String
    let ecid: String
    let hgid: String
    let updatedAt: String
    let previewURL: URL?
    let mergedVideoURL: URL?
    let dataFileURL: URL?
    let matchedRows: Int
    let note: String
    let serverHost: String
    let connected: Bool
    let activeSegments: Int
    let hlsSegmentCount: Int
    let videoCodec: String
    let audioCodec: String
    let effectiveFPS: Double
    let lowFrameRate: Bool
    let lastError: String
    let lastPacketAt: String
    let startedAt: String

    var stageTitle: String {
        switch state {
        case "initializing":
            return "初始化"
        case "polling":
            return "抓取数据"
        case "starting":
            return "启动接流"
        case "connected":
            return "已建连"
        case "recording":
            return "录制中"
        case "retrying":
            return "重试中"
        case "stopping":
            return "停止中"
        case "stopped":
            return "已停止"
        case "completed":
            return "已完成"
        case "failed":
            return "失败"
        case "skipped":
            return "已跳过"
        default:
            return state.isEmpty ? "未知" : state
        }
    }

    var failureSummary: String {
        if !lastError.isEmpty {
            return lastError
        }
        if !stopReason.isEmpty,
           stopReason != "track_eof",
           stopReason != "manual_stop",
           !stopReason.hasPrefix("signal_") {
            return stopReason
        }
        return ""
    }
}

struct AppLogLine: Identifiable, Hashable {
    let id = UUID()
    let timestamp: Date = .now
    let text: String
    let source: String

    init(text: String, source: String = "app") {
        self.text = text
        self.source = source
    }
}

struct BridgePageState: Codable, Equatable {
    var ok: Bool = false
    var webViewReady: Bool = false
    var currentURL: String = ""
    var title: String = ""
    var readyState: String = ""
    var hasLivePane: Bool = false
    var loginRequired: Bool = false
    var liveCandidateCount: Int = 0
    var source: String = "app_web_bridge"
    var error: String = ""

    enum CodingKeys: String, CodingKey {
        case ok
        case webViewReady
        case currentURL
        case title
        case readyState
        case hasLivePane
        case loginRequired
        case liveCandidateCount
        case source
        case error
    }

    init() {}

    init(ok: Bool = false,
         webViewReady: Bool = false,
         currentURL: String = "",
         title: String = "",
         readyState: String = "",
         hasLivePane: Bool = false,
         loginRequired: Bool = false,
         liveCandidateCount: Int = 0,
         source: String = "app_web_bridge",
         error: String = "") {
        self.ok = ok
        self.webViewReady = webViewReady
        self.currentURL = currentURL
        self.title = title
        self.readyState = readyState
        self.hasLivePane = hasLivePane
        self.loginRequired = loginRequired
        self.liveCandidateCount = liveCandidateCount
        self.source = source
        self.error = error
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = try container.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        webViewReady = try container.decodeIfPresent(Bool.self, forKey: .webViewReady) ?? false
        currentURL = try container.decodeIfPresent(String.self, forKey: .currentURL) ?? ""
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        readyState = try container.decodeIfPresent(String.self, forKey: .readyState) ?? ""
        hasLivePane = try container.decodeIfPresent(Bool.self, forKey: .hasLivePane) ?? false
        loginRequired = try container.decodeIfPresent(Bool.self, forKey: .loginRequired) ?? false
        liveCandidateCount = try container.decodeIfPresent(Int.self, forKey: .liveCandidateCount) ?? 0
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? "app_web_bridge"
        error = try container.decodeIfPresent(String.self, forKey: .error) ?? ""
    }
}

struct ArtifactSessionSummary: Codable, Equatable, Identifiable, Hashable {
    var id: String
    var session_dir: String
    var session_name: String
    var mode: String
    var date: String
    var match_dir_name: String
    var active: Bool
    var active_state: String
    var active_job_id: String
    var title: String
    var full_video_count: Int
    var segment_count: Int
    var has_hls: Bool
    var can_delete_directly: Bool

    var displayTitle: String {
        if !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return title
        }
        if !match_dir_name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return match_dir_name
        }
        return session_name
    }

    var modeTitle: String {
        switch mode {
        case "formal":
            return "正式"
        case "test":
            return "测试"
        default:
            return mode.isEmpty ? "未知" : mode
        }
    }
}
