import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia
import AppKit

enum CaptureError: Error, CustomStringConvertible {
    case missingArgument(String)
    case invalidArgument(String)
    case windowNotFound(UInt32)
    case recordingFailed(String)

    var description: String {
        switch self {
        case .missingArgument(let name):
            return "missing argument: \(name)"
        case .invalidArgument(let value):
            return "invalid argument: \(value)"
        case .windowNotFound(let windowID):
            return "window not found: \(windowID)"
        case .recordingFailed(let message):
            return message
        }
    }
}

struct Options {
    let windowID: UInt32
    let outputPath: String
    let fps: Int
    let width: Int
    let height: Int
    let cropLeft: Double
    let cropTop: Double
    let cropWidth: Double
    let cropHeight: Double

    static func parse(_ args: [String]) throws -> Options {
        func value(for flag: String) -> String? {
            guard let idx = args.firstIndex(of: flag), idx + 1 < args.count else {
                return nil
            }
            return args[idx + 1]
        }

        guard let windowRaw = value(for: "--window-id") else {
            throw CaptureError.missingArgument("--window-id")
        }
        guard let windowID = UInt32(windowRaw) else {
            throw CaptureError.invalidArgument(windowRaw)
        }
        guard let outputPath = value(for: "--output"), !outputPath.isEmpty else {
            throw CaptureError.missingArgument("--output")
        }
        let fps = Int(value(for: "--fps") ?? "30") ?? 30
        let width = Int(value(for: "--width") ?? "0") ?? 0
        let height = Int(value(for: "--height") ?? "0") ?? 0
        let cropLeft = Double(value(for: "--crop-left") ?? "0") ?? 0
        let cropTop = Double(value(for: "--crop-top") ?? "0") ?? 0
        let cropWidth = Double(value(for: "--crop-width") ?? "0") ?? 0
        let cropHeight = Double(value(for: "--crop-height") ?? "0") ?? 0

        return Options(
            windowID: windowID,
            outputPath: outputPath,
            fps: max(1, fps),
            width: max(0, width),
            height: max(0, height),
            cropLeft: max(0, cropLeft),
            cropTop: max(0, cropTop),
            cropWidth: max(0, cropWidth),
            cropHeight: max(0, cropHeight)
        )
    }
}

func logLine(_ message: String) {
    fputs("[window_capture] \(message)\n", stderr)
    fflush(stderr)
}

func getShareableContent() async throws -> SCShareableContent {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<SCShareableContent, Error>) in
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { content, error in
            if let error {
                continuation.resume(throwing: error)
                return
            }
            guard let content else {
                continuation.resume(throwing: CaptureError.recordingFailed("shareable content unavailable"))
                return
            }
            continuation.resume(returning: content)
        }
    }
}

func startCapture(_ stream: SCStream) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
        stream.startCapture { error in
            if let error {
                continuation.resume(throwing: error)
            } else {
                continuation.resume(returning: ())
            }
        }
    }
}

func stopCapture(_ stream: SCStream) async {
    await withCheckedContinuation { continuation in
        stream.stopCapture { _ in
            continuation.resume(returning: ())
        }
    }
}

final class RecordingDelegate: NSObject, SCRecordingOutputDelegate {
    private var startedContinuation: CheckedContinuation<Void, Error>?
    private var finishedContinuation: CheckedContinuation<Void, Error>?
    private var started = false
    private var finished = false
    private var finishError: Error?
    private var stopRequested = false

    func markStopRequested() {
        stopRequested = true
    }

    func waitForStart() async throws {
        if let finishError {
            throw finishError
        }
        if started {
            return
        }
        try await withCheckedThrowingContinuation { continuation in
            self.startedContinuation = continuation
        }
    }

    func waitForFinish() async throws {
        if let finishError {
            throw finishError
        }
        if finished {
            return
        }
        try await withCheckedThrowingContinuation { continuation in
            self.finishedContinuation = continuation
        }
    }

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        started = true
        logLine("recording started")
        startedContinuation?.resume(returning: ())
        startedContinuation = nil
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: any Error) {
        finishError = error
        logLine("recording failed: \(error.localizedDescription) stop_requested=\(stopRequested)")
        startedContinuation?.resume(throwing: error)
        startedContinuation = nil
        finishedContinuation?.resume(throwing: error)
        finishedContinuation = nil
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        finished = true
        logLine("recording finished stop_requested=\(stopRequested)")
        finishedContinuation?.resume(returning: ())
        finishedContinuation = nil
    }
}

@MainActor
final class CaptureRunner: NSObject, SCStreamDelegate {
    private let options: Options
    private let delegate = RecordingDelegate()
    private var signalSources: [DispatchSourceSignal] = []
    private var stream: SCStream?
    private var isStopping = false

    init(options: Options) {
        self.options = options
        super.init()
    }

    private func installSignals() {
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        for sig in [SIGINT, SIGTERM] {
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in
                guard let self else { return }
                Task { await self.stop() }
            }
            source.resume()
            signalSources.append(source)
        }
    }

    private func makeSourceRect(_ fullRect: CGRect) -> CGRect {
        // For desktop-independent window capture, sourceRect is relative to window origin
        // Use full window content if no crop specified
        var width = options.cropWidth > 0 ? options.cropWidth : fullRect.width
        var height = options.cropHeight > 0 ? options.cropHeight : fullRect.height
        var originX = options.cropLeft
        var originY = options.cropTop

        // Clamp to valid bounds
        width = max(64.0, min(width, fullRect.width - originX))
        height = max(64.0, min(height, fullRect.height - originY))
        originX = max(0.0, min(originX, fullRect.width - width))
        originY = max(0.0, min(originY, fullRect.height - height))

        return CGRect(x: originX, y: originY, width: width, height: height).integral
    }

    func start() async throws {
        let content = try await getShareableContent()
        guard let window = content.windows.first(where: { $0.windowID == options.windowID }) else {
            throw CaptureError.windowNotFound(options.windowID)
        }

        let filter = SCContentFilter(desktopIndependentWindow: window)
        let info = SCShareableContent.info(for: filter)
        let sourceRect = makeSourceRect(info.contentRect)
        let scale = max(1.0, Double(info.pointPixelScale))

        let config = SCStreamConfiguration()
        config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(options.fps))
        config.showsCursor = false
        config.capturesAudio = false
        config.captureMicrophone = false
        config.scalesToFit = true
        config.preservesAspectRatio = true
        config.ignoreShadowsSingleWindow = true
        config.ignoreGlobalClipSingleWindow = true
        config.shouldBeOpaque = true
        config.sourceRect = sourceRect
        config.width = options.width > 0 ? options.width : Int((sourceRect.width * scale).rounded())
        config.height = options.height > 0 ? options.height : Int((sourceRect.height * scale).rounded())

        let recordingConfig = SCRecordingOutputConfiguration()
        recordingConfig.outputURL = URL(fileURLWithPath: options.outputPath)
        recordingConfig.videoCodecType = .h264
        recordingConfig.outputFileType = .mp4

        let recordingOutput = SCRecordingOutput(configuration: recordingConfig, delegate: delegate)
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addRecordingOutput(recordingOutput)

        self.stream = stream
        installSignals()

        logLine(
            "window=\(options.windowID) sourceRect=\(NSStringFromRect(sourceRect)) " +
            "output=\(config.width)x\(config.height)"
        )

        try await startCapture(stream)
        try await delegate.waitForStart()
    }

    func stop() async {
        guard !isStopping else { return }
        isStopping = true
        guard let stream else { return }
        delegate.markStopRequested()
        logLine("stopping capture (requested by helper)")
        await stopCapture(stream)
    }

    func waitUntilFinished() async throws {
        try await delegate.waitForFinish()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        logLine("stream didStopWithError: \(error.localizedDescription)")
    }
}

@main
struct WindowCaptureMain {
    static func main() async {
        do {
            let options = try Options.parse(CommandLine.arguments.dropFirst().map { $0 })
            _ = NSApplication.shared
            NSApp.setActivationPolicy(.prohibited)
            let parentDir = URL(fileURLWithPath: options.outputPath).deletingLastPathComponent()
            try FileManager.default.createDirectory(at: parentDir, withIntermediateDirectories: true)

            let runner = CaptureRunner(options: options)
            try await runner.start()
            try await runner.waitUntilFinished()
        } catch {
            logLine("fatal: \(error)")
            exit(1)
        }
    }
}
