import Foundation
import ShazamKit
@preconcurrency import AVFoundation

@MainActor
final class RecognizeModel: ObservableObject {
    @Published var urlText: String = ""
    @Published var isRunning: Bool = false
    @Published var errorText: String? = nil
    @Published var outputPath: String? = nil
    @Published var matchesPath: String? = nil
    @Published var stepText: String? = nil
    @Published var liveOutput: String = ""
    @Published var currentJobID: UUID? = nil
    @Published var recognizedTracks: [RecognizedTrack] = []
    @Published var telegramIsSending: Bool = false
    @Published var telegramStatusText: String? = nil
    @Published var enableMatching: Bool = true
    @Published private(set) var occurrences: [TrackOccurrence] = []
    @Published private(set) var scanProgressSeconds: Double? = nil
    @Published private(set) var scanTotalSeconds: Double? = nil
    @Published private(set) var artifactsStatusText: String? = nil

    private let runner = ProcessRunner()
    private var currentTask: Task<Void, Never>? = nil
    private weak var jobsStoreRef: JobsStore? = nil
    // Updated as we feed streaming audio; used to timestamp match callbacks.
    private var currentQuerySeconds: Double = 0

    private let defaultsLastURLKey = "djtools.recognize.lastURL"
    private let defaultsLastMatchesPathKey = "djtools.recognize.lastMatchesPath"

    func restoreLastResultsIfAny(repo: URL) {
        guard !isRunning else { return }
        // If we already have data, don't overwrite it.
        if !occurrences.isEmpty || matchesPath != nil { return }

        if let lastURL = UserDefaults.standard.string(forKey: defaultsLastURLKey),
           !lastURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        {
            urlText = lastURL
        }

        // Prefer explicit remembered matches path
        if let p = UserDefaults.standard.string(forKey: defaultsLastMatchesPathKey),
           !p.isEmpty,
           FileManager.default.fileExists(atPath: p)
        {
            if loadMatchesFromDisk(matchesJSONPath: p) {
                artifactsStatusText = "Loaded last run results."
                return
            }
        }

        // Fallback: scan logs/recognize/*/matches.json and pick latest.
        let recognizeRoot = repo.appendingPathComponent("logs/recognize", isDirectory: true)
        guard let latest = findLatestMatchesJSON(recognizeRoot: recognizeRoot) else { return }
        if loadMatchesFromDisk(matchesJSONPath: latest.path) {
            UserDefaults.standard.set(latest.path, forKey: defaultsLastMatchesPathKey)
            artifactsStatusText = "Loaded last run results."
        }
    }

    private func findLatestMatchesJSON(recognizeRoot: URL) -> URL? {
        guard FileManager.default.fileExists(atPath: recognizeRoot.path) else { return nil }
        guard let dirs = try? FileManager.default.contentsOfDirectory(
            at: recognizeRoot,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }

        let candidates: [URL] = dirs.compactMap { d in
            let isDir = (try? d.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            guard isDir else { return nil }
            if d.lastPathComponent == "_debug" { return nil }
            let m = d.appendingPathComponent("matches.json", isDirectory: false)
            guard FileManager.default.fileExists(atPath: m.path) else { return nil }
            return m
        }

        return candidates.max { a, b in
            let ad = (try? a.deletingLastPathComponent().resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let bd = (try? b.deletingLastPathComponent().resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ad < bd
        }
    }

    private func loadMatchesFromDisk(matchesJSONPath: String) -> Bool {
        do {
            let data = try Data(contentsOf: URL(fileURLWithPath: matchesJSONPath))
            let occ = try JSONDecoder().decode([TrackOccurrence].self, from: data)
            occurrences = occ
            recognizedTracks = occ.map {
                RecognizedTrack(
                    isrc: $0.isrc,
                    title: $0.title,
                    artist: $0.artist,
                    appleMusicURL: $0.appleMusicURL,
                    webURL: $0.webURL,
                    artworkURL: $0.artworkURL
                )
            }
            matchesPath = matchesJSONPath
            errorText = nil
            return true
        } catch {
            return false
        }
    }

    struct TrackOccurrence: Identifiable, Codable, Hashable {
        var id: UUID = UUID()
        var queryTimeSeconds: Double
        var estimatedTrackStartSeconds: Double?

        var shazamID: String?
        var isrc: String?
        var title: String
        var artist: String?
        var appleMusicURL: String?
        var webURL: String?
        var artworkURL: String?

        var matchOffsetSeconds: Double?
        var predictedCurrentMatchOffsetSeconds: Double?
        var frequencySkew: Float?
        var confidence: Float?
    }

    struct RecognizedTrack: Identifiable, Codable, Hashable {
        var id: UUID = UUID()
        var isrc: String?
        var title: String
        var artist: String?
        var appleMusicURL: String?
        var webURL: String?
        var artworkURL: String?
    }

    func startDownload(repo: URL, config: DJToolsConfig?, jobsStore: JobsStore) {
        guard !isRunning else { return }
        jobsStoreRef = jobsStore
        currentTask = Task { [weak self] in
            guard let self else { return }
            _ = await self.downloadAudio(repo: repo, config: config, jobsStore: jobsStore, alsoPrintToStdout: false)
        }
    }

    func cancel() {
        guard isRunning else { return }
        stepText = "Cancelling…"
        runner.terminate()
        currentTask?.cancel()
    }

    static func formatTimestamp(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        let h = s / 3600
        let m = (s % 3600) / 60
        let sec = s % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
        return String(format: "%d:%02d", m, sec)
    }

    @discardableResult
    func runDownloadForCLI(repo: URL, config: DJToolsConfig?, jobsStore: JobsStore, url: String) async -> Bool {
        jobsStoreRef = jobsStore
        urlText = url
        return await downloadAudio(repo: repo, config: config, jobsStore: jobsStore, alsoPrintToStdout: true)
    }

    @discardableResult
    func runMatchFileForCLI(wavURL: URL) async -> Bool {
        do {
            let items = try await matchAudioFile(url: wavURL) { s in
                FileHandle.standardOutput.write(Data(s.utf8))
            }
            let tracks: [RecognizedTrack] = items.map { item in
                RecognizedTrack(
                    isrc: item.isrc,
                    title: item.title ?? "(unknown title)",
                    artist: item.artist,
                    appleMusicURL: item.appleMusicURL?.absoluteString,
                    webURL: item.webURL?.absoluteString,
                    artworkURL: item.artworkURL?.absoluteString
                )
            }
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            let json = (try? enc.encode(tracks)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
            FileHandle.standardOutput.write(Data(("\n--- matches ---\n" + json + "\n").utf8))
            return true
        } catch {
            FileHandle.standardError.write(Data(("\nERROR: \(error)\n").utf8))
            return false
        }
    }

    private func clampFragments(_ n: Int) -> Int {
        // yt-dlp accepts 1..N; keep this conservative to avoid surprising resource spikes.
        return min(32, max(1, n))
    }

    private func resolveToolPath(repo: URL, configValue: String?, defaultRelative: String) -> String {
        let raw = (configValue?.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap { $0.isEmpty ? nil : $0 } ?? defaultRelative
        if raw.hasPrefix("/") { return raw }
        return repo.appendingPathComponent(raw).path
    }

    private func appendToLiveOutput(_ s: String) {
        // Keep UI responsive: cap in-memory log to last ~200 KB.
        liveOutput.append(s)
        let maxChars = 200_000
        if liveOutput.count > maxChars {
            liveOutput = String(liveOutput.suffix(maxChars))
        }
    }

    private func downloadAudio(repo: URL, config: DJToolsConfig?, jobsStore: JobsStore, alsoPrintToStdout: Bool) async -> Bool {
        isRunning = true
        defer {
            isRunning = false
            stepText = nil
            currentTask = nil
        }

        errorText = nil
        outputPath = nil
        matchesPath = nil
        stepText = nil
        liveOutput = ""
        recognizedTracks = []
        occurrences = []
        scanProgressSeconds = nil
        scanTotalSeconds = nil
        artifactsStatusText = nil
        telegramStatusText = nil

        let inputURL = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        if inputURL.isEmpty {
            errorText = "Please paste a URL."
            return false
        }
        UserDefaults.standard.set(inputURL, forKey: defaultsLastURLKey)

        let logsDir = repo.appendingPathComponent("logs", isDirectory: true)
        let recognizeDir = logsDir.appendingPathComponent("recognize", isDirectory: true)
        try? FileManager.default.createDirectory(at: recognizeDir, withIntermediateDirectories: true)

        let ts = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
        let workDir = recognizeDir.appendingPathComponent(ts, isDirectory: true)
        try? FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)

        let logURL = workDir.appendingPathComponent("recognize.log", isDirectory: false)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)

        let logHandle: FileHandle? = try? FileHandle(forWritingTo: logURL)
        defer { try? logHandle?.close() }

        func logLine(_ s: String) {
            appendToLiveOutput(s)
            if let data = s.data(using: .utf8) {
                _ = try? logHandle?.seekToEnd()
                _ = try? logHandle?.write(contentsOf: data)
            }
            if alsoPrintToStdout {
                // Print without adding extra newlines; yt-dlp already emits them.
                FileHandle.standardOutput.write(Data(s.utf8))
            }
        }

        let ytdlp = resolveToolPath(repo: repo, configValue: config?.ytDlpPath, defaultRelative: "tools/bin/yt-dlp")
        let ffmpeg = resolveToolPath(repo: repo, configValue: config?.ffmpegPath, defaultRelative: "tools/bin/ffmpeg")
        let toolsBin = repo.appendingPathComponent("tools/bin", isDirectory: true).path

        let wavOut = workDir.appendingPathComponent("normalized.wav").path
        outputPath = wavOut

        let matchesJSON = workDir.appendingPathComponent("matches.json").path
        let matchesTXT = workDir.appendingPathComponent("matches.txt").path
        matchesPath = matchesJSON
        UserDefaults.standard.set(matchesJSON, forKey: defaultsLastMatchesPathKey)

        let fragments = clampFragments(config?.recognizeConcurrentFragments ?? 8)

        let baseEnvPath = ProcessInfo.processInfo.environment["PATH"] ?? ""
        let env: [String: String] = [
            // GUI apps often have a minimal PATH; include our bundled tools/bin explicitly.
            "PATH": "\(toolsBin):/usr/bin:/bin:/usr/sbin:/sbin:\(baseEnvPath)",
        ]

        // Create a job so it shows up in the Jobs tab while running.
        var job = Job(
            title: "Recognize: download + normalize + match",
            commandLine: ["yt-dlp", "--concurrent-fragments", "\(fragments)", inputURL],
            workingDirectory: workDir.path,
            status: .running
        )
        job.startedAt = Date()
        job.artifactsPath = workDir.path
        job.logPath = logURL.path
        job.finalPath = matchesJSON
        jobsStore.add(job)
        currentJobID = job.id

        func failJob(exit: Int32?, message: String) {
            job.status = .failed
            job.exitCode = exit
            job.endedAt = Date()
            job.errorMessage = message
            jobsStore.update(job)
            errorText = message
            if alsoPrintToStdout {
                FileHandle.standardError.write(Data(("\nERROR: \(message)\n").utf8))
            }
        }

        func cancelJob(message: String = "Cancelled.") {
            job.status = .cancelled
            job.exitCode = nil
            job.endedAt = Date()
            job.errorMessage = message
            jobsStore.update(job)
            errorText = nil
            stepText = message
        }

        func succeedJob() {
            job.status = .succeeded
            job.exitCode = 0
            job.endedAt = Date()
            jobsStore.update(job)
        }

        // 1) yt-dlp: download best audio only (no postprocessing here).
        stepText = "Downloading audio (yt-dlp)…"
        do {
            let result = try await runner.run(
                argv: [
                    ytdlp,
                    "-f", "bestaudio/best",
                    "--no-playlist",
                    "--concurrent-fragments", "\(fragments)",
                    "--newline",
                    "-o", "raw.%(ext)s",
                    inputURL
                ],
                cwd: workDir,
                environment: env,
                onLine: logLine
            )
            if Task.isCancelled {
                cancelJob()
                return false
            }
            if result.exitCode != 0 {
                if Task.isCancelled {
                    cancelJob()
                    return false
                }
                failJob(exit: result.exitCode, message: "yt-dlp failed (exit \(result.exitCode)). See output/log.")
                return false
            }
        } catch {
            if (error is CancellationError) || Task.isCancelled {
                cancelJob()
                return false
            }
            failJob(exit: nil, message: "yt-dlp failed: \(error)")
            return false
        }

        // Pick the downloaded audio file (largest non-wav file in workDir).
        let audioInput: String
        do {
            let files = try FileManager.default.contentsOfDirectory(at: workDir, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey])
            let candidates: [(url: URL, size: Int)] = files.compactMap { u in
                guard u.lastPathComponent != "normalized.wav" else { return nil }
                guard u.lastPathComponent != "recognize.log" else { return nil }
                guard (try? u.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else { return nil }
                let size = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                guard size > 0 else { return nil }
                return (u, size)
            }
            if let best = candidates.max(by: { $0.size < $1.size }) {
                audioInput = best.url.path
            } else {
                failJob(exit: nil, message: "No audio file produced by yt-dlp. See output/log.")
                return false
            }
        } catch {
            failJob(exit: nil, message: "Failed to read output folder: \(error)")
            return false
        }

        // 2) ffmpeg normalize -> PCM wav tuned for ShazamKit matching.
        // Prefer 48kHz (common for YouTube Opus) to avoid unnecessary resampling artifacts.
        stepText = "Normalizing audio (ffmpeg)…"
        do {
            let result = try await runner.run(
                argv: [
                    ffmpeg,
                    "-y",
                    "-i", audioInput,
                    "-vn",
                    "-ac", "2",
                    "-ar", "48000",
                    // Gentle loudness normalization (avoid hard clipping / weird limiting).
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-c:a", "pcm_s16le",
                    wavOut
                ],
                cwd: repo,
                environment: env,
                onLine: logLine
            )
            if Task.isCancelled {
                cancelJob()
                return false
            }
            if result.exitCode != 0 {
                if Task.isCancelled {
                    cancelJob()
                    return false
                }
                failJob(exit: result.exitCode, message: "ffmpeg failed (exit \(result.exitCode)). See output/log.")
                return false
            }
        } catch {
            if (error is CancellationError) || Task.isCancelled {
                cancelJob()
                return false
            }
            failJob(exit: nil, message: "ffmpeg failed: \(error)")
            return false
        }

        // 3) ShazamKit match the normalized audio
        if !enableMatching {
            recognizedTracks = []
            occurrences = []
            stepText = nil
            try? Data("[]\n".utf8).write(to: URL(fileURLWithPath: matchesJSON), options: [.atomic])
            try? "Matching disabled in UI.\n".data(using: .utf8)?.write(to: URL(fileURLWithPath: matchesTXT), options: [.atomic])
            logLine("\nMatching disabled. Saved placeholder matches:\n- \(matchesJSON)\n- \(matchesTXT)\n")
            succeedJob()
            return true
        }

        do {
            // Always do the “mix scan” style scan (it also works fine for single tracks).
            stepText = "Scanning (ShazamKit)…"
            let rawOcc = try await scanMixtape(url: URL(fileURLWithPath: wavOut), logLine: logLine)
            if Task.isCancelled {
                cancelJob()
                return false
            }
            let occ = dedupeOccurrences(rawOcc)
            occurrences = occ

            // Also provide a “flat” track list derived from occurrences (deduped).
            recognizedTracks = occ.map {
                RecognizedTrack(isrc: $0.isrc, title: $0.title, artist: $0.artist, appleMusicURL: $0.appleMusicURL, webURL: $0.webURL, artworkURL: $0.artworkURL)
            }

            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            let json = try enc.encode(occ)
            try json.write(to: URL(fileURLWithPath: matchesJSON), options: [.atomic])

            let txt: String
            if occ.isEmpty {
                txt = "No matches.\n"
            } else {
                txt = occ.enumerated().map { i, o in
                    let ts = Self.formatTimestamp(o.estimatedTrackStartSeconds ?? o.queryTimeSeconds)
                    let artist = o.artist.map { " — \($0)" } ?? ""
                    let link = o.webURL ?? o.appleMusicURL
                    if let link {
                        return "\(i + 1). [\(ts)] \(o.title)\(artist)\n   \(link)\n"
                    }
                    return "\(i + 1). [\(ts)] \(o.title)\(artist)\n"
                }.joined(separator: "\n")
            }
            try txt.data(using: .utf8)?.write(to: URL(fileURLWithPath: matchesTXT), options: [.atomic])
            logLine("\nSaved scan results:\n- \(matchesJSON)\n- \(matchesTXT)\n")

            // Delete large artifacts after a successful run (keep only matches + log).
            cleanupRecognizeWorkDir(workDir: workDir, keepFileNames: ["recognize.log", "matches.json", "matches.txt"])
        } catch {
            if (error is CancellationError) || Task.isCancelled {
                cancelJob()
                return false
            }
            let ns = error as NSError
            if ns.domain == "com.apple.ShazamKit", ns.code == 202 {
                failJob(exit: nil, message: """
ShazamKit is not enabled for this app build.

To enable:
- Open the Xcode project target (DJToolsApp)
- Signing & Capabilities → add “ShazamKit”
- Select a Team and run a signed build
- Ensure the bundle id has the ShazamKit App Service enabled

Underlying: \(ns.localizedDescription)
""")
            } else {
                failJob(exit: nil, message: "ShazamKit match failed: \(error)")
            }
            return false
        }

        succeedJob()
        stepText = nil
        return true
    }

    func sendMatchesToTelegram(config: DJToolsConfig?) async {
        guard !telegramIsSending else { return }
        telegramStatusText = nil

        let token = config?.telegramBotToken?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let chatID = normalizeTelegramChatID(config?.telegramChatID)
        guard !token.isEmpty, !chatID.isEmpty else {
            telegramStatusText = "Telegram not configured (missing telegram_bot_token or telegram_chat_id)."
            return
        }

        telegramIsSending = true
        defer { telegramIsSending = false }

        let msg = formatTelegramMessage(url: urlText, tracks: recognizedTracks, occurrences: occurrences)
        do {
            try await telegramSendMessage(token: token, chatID: chatID, text: msg)
            telegramStatusText = "Sent to Telegram."
        } catch {
            telegramStatusText = telegramFriendlyError(error)
        }
    }

    func sendSpotifyPlaylistToTelegram(
        config: DJToolsConfig?,
        playlistURL: URL,
        chatIDOverride: String?
    ) async {
        guard !telegramIsSending else { return }
        telegramStatusText = nil

        let token = config?.telegramBotToken?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let defaultChatID = normalizeTelegramChatID(config?.telegramChatID)
        let override = normalizeTelegramChatID(chatIDOverride)
        let chatID = override.isEmpty ? defaultChatID : override

        guard !token.isEmpty, !chatID.isEmpty else {
            telegramStatusText = "Telegram not configured (missing telegram_bot_token or telegram_chat_id / @username)."
            return
        }

        telegramIsSending = true
        defer { telegramIsSending = false }

        let msg = formatTelegramPlaylistMessage(
            url: urlText,
            playlistURL: playlistURL,
            trackCount: occurrences.count
        )
        do {
            try await telegramSendMessage(token: token, chatID: chatID, text: msg)
            telegramStatusText = "Sent playlist link to Telegram."
        } catch {
            telegramStatusText = telegramFriendlyError(error)
        }
    }
}

// MARK: - ShazamKit matching

private final class ShazamSessionDelegate: NSObject, SHSessionDelegate {
    private var _continuation: CheckedContinuation<[SHMatchedMediaItem], Error>?

    /// Setting a new continuation cancels any stale/leaked one from a previous match.
    var continuation: CheckedContinuation<[SHMatchedMediaItem], Error>? {
        get { _continuation }
        set {
            // If a previous continuation was never resumed (leaked), cancel it now
            // so the caller doesn't hang forever.
            if let old = _continuation {
                old.resume(returning: [])
            }
            _continuation = newValue
        }
    }

    func session(_ session: SHSession, didFind match: SHMatch) {
        _continuation?.resume(returning: match.mediaItems)
        _continuation = nil
    }

    func session(_ session: SHSession, didNotFindMatchFor signature: SHSignature, error: Error?) {
        if let error {
            _continuation?.resume(throwing: error)
        } else {
            _continuation?.resume(returning: [])
        }
        _continuation = nil
    }
}

private extension RecognizeModel {
    func normalizeTelegramChatID(_ s: String?) -> String {
        let raw = (s ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return "" }
        // Numeric ids: "123…" or "-100…"
        let isNumeric = raw.allSatisfy { $0.isNumber } || (raw.hasPrefix("-") && raw.dropFirst().allSatisfy { $0.isNumber })
        if isNumeric { return raw }
        // Usernames should be "@name". If user typed "name", prefix "@"
        if raw.hasPrefix("@") { return raw }
        return "@\(raw)"
    }

    func telegramFriendlyError(_ error: Error) -> String {
        let msg = (error as NSError).localizedDescription
        if msg.localizedCaseInsensitiveContains("chat not found") {
            return """
Telegram chat not found.

Notes:
- Bots cannot DM a person by @username. The user must message the bot first, then use their numeric chat_id.
- For channels/supergroups: ensure the bot is added (often as admin) and use the @channelusername (public username) or the numeric -100… chat id.
"""
        }
        return "Telegram send failed: \(error)"
    }

    func formatTelegramPlaylistMessage(url: String, playlistURL: URL, trackCount: Int) -> String {
        let cleanURL = url.trimmingCharacters(in: .whitespacesAndNewlines)
        var lines: [String] = []
        lines.append("dj-tools")
        if !cleanURL.isEmpty { lines.append(cleanURL) }
        lines.append("")
        lines.append("Spotify playlist:")
        lines.append(playlistURL.absoluteString)
        if trackCount > 0 {
            lines.append("Tracks: \(trackCount)")
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
    }

    func matchAudioFile(url: URL, logLine: (String) -> Void) async throws -> [SHMatchedMediaItem] {
        // Use a longer window first, then fall back to a shorter one if needed.
        // Empirically, 10–12s works well for ShazamKit queries.
        let signatureLengths: [Double] = [12.0, 10.0]

        // For long mixes, the first seconds often contain intro/voiceover/transition.
        // Try a few sliding windows before concluding "no match".
        let offsetsToTry: [Double] = [0, 60, 120, 180, 240, 300]

        let session = SHSession()
        let delegate = ShazamSessionDelegate()
        session.delegate = delegate

        for offset in offsetsToTry {
            for sigLen in signatureLengths {
                logLine("\n[shazamkit] generating signature (offset \(Self.formatTimestamp(offset)), \(Int(sigLen))s)…\n")
                let sigURL = url, sigStart = offset, sigLen = sigLen
                let signature = try await Task.detached { [self] in
                    try self.generateSignatureFromAudioFile(url: sigURL, startSeconds: sigStart, maxSeconds: sigLen)
                }.value

                logLine("[shazamkit] matching…\n")
                do {
                    let items: [SHMatchedMediaItem] = try await withCheckedThrowingContinuation { cont in
                        delegate.continuation = cont
                        session.match(signature)
                    }
                    if !items.isEmpty {
                        return items
                    }
                } catch {
                    // If ShazamKit rejects the window (too short/silent) or transiently fails, keep trying next offset.
                    logLine("[shazamkit] window failed at \(Self.formatTimestamp(offset)) (\(Int(sigLen))s): \(error)\n")
                }
            }
            logLine("[shazamkit] no match at \(Self.formatTimestamp(offset))\n")
        }

        return []
    }

    nonisolated func generateSignatureFromAudioFile(url: URL, startSeconds: Double, maxSeconds: Double) throws -> SHSignature {
        // Feed SHSignatureGenerator with AVAudioTime provided by an engine tap.
        // This matches Apple's recommended usage and avoids the "audio is not contiguous"
        // error we've been seeing when synthesizing AVAudioTime ourselves.
        let file = try AVAudioFile(forReading: url)
        let inFormat = file.processingFormat

        guard let tapFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: inFormat.sampleRate,
            channels: min(2, inFormat.channelCount),
            interleaved: false
        ) else {
            throw NSError(domain: "djtools.shazamkit", code: 1, userInfo: [NSLocalizedDescriptionKey: "Failed to create tap audio format"])
        }

        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: tapFormat)
        engine.mainMixerNode.outputVolume = 0.0 // don't play through speakers

        let sigGen = SHSignatureGenerator()
        let maxFrames = AVAudioFramePosition(tapFormat.sampleRate * maxSeconds)

        final class TapState: @unchecked Sendable {
            let lock = NSLock()
            var firstSampleTime: AVAudioFramePosition? = nil
            var done: Bool = false
            var error: Error? = nil
        }
        let state = TapState()
        let sem = DispatchSemaphore(value: 0)

        engine.mainMixerNode.installTap(onBus: 0, bufferSize: 4096, format: tapFormat) { buffer, time in
            state.lock.lock()
            if state.done {
                state.lock.unlock()
                return
            }
            if state.firstSampleTime == nil {
                state.firstSampleTime = time.sampleTime
            }
            let start = state.firstSampleTime ?? time.sampleTime
            let progressed = time.sampleTime - start
            if progressed >= maxFrames {
                state.done = true
                state.lock.unlock()
                sem.signal()
                return
            }
            state.lock.unlock()

            do {
                try sigGen.append(buffer, at: time)
            } catch {
                state.lock.lock()
                state.error = error
                state.done = true
                state.lock.unlock()
                sem.signal()
                return
            }

            // Stop once we’ve accumulated enough timeline.
            state.lock.lock()
            let progressedAfter = (time.sampleTime - (state.firstSampleTime ?? time.sampleTime)) + AVAudioFramePosition(buffer.frameLength)
            if progressedAfter >= maxFrames {
                state.done = true
                state.lock.unlock()
                sem.signal()
                return
            }
            state.lock.unlock()
        }

        do {
            try engine.start()
        } catch {
            engine.mainMixerNode.removeTap(onBus: 0)
            throw error
        }

        // Schedule a segment starting at startSeconds for maxSeconds.
        let sr = tapFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, startSeconds) * sr)
        let maxSegmentFrames = AVAudioFramePosition(maxSeconds * sr)
        let available = max(0, file.length - startFrame)
        let segmentFrames = AVAudioFrameCount(min(available, maxSegmentFrames))
        if segmentFrames == 0 {
            engine.mainMixerNode.removeTap(onBus: 0)
            engine.stop()
            return sigGen.signature()
        }

        player.scheduleSegment(file, startingFrame: startFrame, frameCount: segmentFrames, at: nil)
        player.play()

        // Give it a little extra headroom beyond maxSeconds.
        _ = sem.wait(timeout: .now() + maxSeconds + 5.0)

        player.stop()
        engine.mainMixerNode.removeTap(onBus: 0)
        engine.stop()

        if let err = state.error {
            throw err
        }

        return sigGen.signature()
    }

    func scanMixtape(url: URL, logLine: (String) -> Void) async throws -> [TrackOccurrence] {
        // Trackid.net-style scan: run many *independent* matches on short windows across the mix.
        // This is much more reliable than trying to "stream" an offline file quickly.

        let asset = AVURLAsset(url: url)
        let durationSeconds = (try? await asset.load(.duration).seconds).flatMap { $0.isFinite ? $0 : nil } ?? 0
        scanTotalSeconds = durationSeconds > 0 ? durationSeconds : nil

        // Tuning (adaptive):
        // - defaultStride: good balance for electronic mixes (3–6min tracks)
        // - minStride: tighten probing when we lose lock (no matches for a while)
        // - window lengths: try longer window, then a shorter window
        let windowLengths: [Double] = [12.0, 10.0]
        let defaultStride: Double = 120.0
        let minStride: Double = 30.0
        let extraProbeOffsets: [Double] = [60.0, 120.0] // probe around a found match
        let maxWindows = 240 // cap to avoid runaway scans (e.g. 2 hours @ 30s stride)

        logLine("\n[shazamkit] windowed scan (adaptive)…\n")

        let session = SHSession()
        let delegate = ShazamSessionDelegate()
        session.delegate = delegate

        var occ: [TrackOccurrence] = []
        func addOccurrence(from item: SHMatchedMediaItem, queryTime: Double) {
            let title = item.title ?? "(unknown title)"
            let artist = item.artist ?? "(unknown artist)"
            let skew = item.frequencySkew
            let confText: String = {
                if #available(macOS 15.4, *) { return String(format: "%.2f", item.confidence) }
                return "n/a"
            }()
            logLine("[shazamkit] candidate: \(title) — \(artist) (skew \(String(format: "%.3f", skew)), conf \(confText))\n")

            // Reject likely-bad matches, but use relaxed thresholds.
            if abs(skew) > 0.18 {
                logLine("[shazamkit] reject: frequency skew too high (\(String(format: "%.3f", skew)))\n")
                return
            }
            if #available(macOS 15.4, *) {
                if item.confidence < 0.15 {
                    logLine("[shazamkit] reject: low confidence (\(String(format: "%.2f", item.confidence)))\n")
                    return
                }
            }
            let shazamID = item.shazamID
            // De-dupe: same track within 60s => ignore.
            if let last = occ.last, last.shazamID == shazamID, abs(last.queryTimeSeconds - queryTime) < 60 {
                return
            }
            let estStart = (item.matchOffset != 0) ? (queryTime - item.matchOffset) : nil
            let o = TrackOccurrence(
                queryTimeSeconds: queryTime,
                estimatedTrackStartSeconds: estStart.map { max(0, $0) },
                shazamID: shazamID,
                isrc: item.isrc,
                title: item.title ?? "(unknown title)",
                artist: item.artist,
                appleMusicURL: item.appleMusicURL?.absoluteString,
                webURL: item.webURL?.absoluteString,
                artworkURL: item.artworkURL?.absoluteString,
                matchOffsetSeconds: item.matchOffset,
                predictedCurrentMatchOffsetSeconds: item.predictedCurrentMatchOffset,
                frequencySkew: item.frequencySkew,
                confidence: {
                    if #available(macOS 15.4, *) { return item.confidence }
                    return nil
                }()
            )
            occ.append(o)
        }

        let totalToScan = durationSeconds > 0 ? durationSeconds : (Double(maxWindows) * defaultStride)
        let windowsCount = min(maxWindows, Int(ceil(totalToScan / defaultStride)))

        // Scheduler: a queue of times to probe (sorted), plus adaptive stride.
        var queue: [Double] = [0.0]
        var visited = Set<Int>() // seconds rounded
        var nextRegularTime: Double = defaultStride
        var currentStride: Double = defaultStride
        var noMatchStreak = 0

        func schedule(_ t: Double) {
            let tt = max(0, min(totalToScan, t))
            let k = Int(tt.rounded())
            guard !visited.contains(k) else { return }
            visited.insert(k)
            let idx = queue.firstIndex(where: { $0 > tt }) ?? queue.count
            queue.insert(tt, at: idx)
        }

        visited.insert(0)
        for i in 1..<windowsCount {
            schedule(Double(i) * defaultStride)
        }

        var windowIndex = 0
        let startedAt = Date()
        while let t = queue.first {
            try Task.checkCancellation()

            scanProgressSeconds = t
            let pct = (scanTotalSeconds ?? totalToScan) > 0 ? (t / (scanTotalSeconds ?? totalToScan)) : 0
            let elapsed = Date().timeIntervalSince(startedAt)
            let avgPerWindow = windowIndex > 0 ? (elapsed / Double(windowIndex)) : 0
            let remainingWindows = max(0, windowsCount - windowIndex)
            let eta = avgPerWindow > 0 ? Int(avgPerWindow * Double(remainingWindows)) : 0
            let etaText = eta > 0 ? " • ETA ~\(eta / 60)m\(eta % 60)s" : ""
            stepText = "Scanning… \(Self.formatTimestamp(t)) / \(Self.formatTimestamp(scanTotalSeconds ?? totalToScan)) (\(Int(pct * 100))%)\(etaText)"

            windowIndex += 1
            logLine("[shazamkit] window \(windowIndex)/\(windowsCount) @ \(Self.formatTimestamp(t))\n")
            queue.removeFirst()

            var matchedThisWindow = false
            for win in windowLengths {
                let signature: SHSignature
                do {
                    // Run on a background thread to avoid blocking the main actor
                    // (the semaphore inside blocks the calling thread).
                    let sigURL = url, sigStart = t, sigWin = win
                    signature = try await Task.detached { [self] in
                        try self.generateSignatureFromAudioFile(url: sigURL, startSeconds: sigStart, maxSeconds: sigWin)
                    }.value
                } catch {
                    logLine("[shazamkit] signature failed @ \(Self.formatTimestamp(t)) (\(Int(win))s): \(error)\n")
                    continue
                }

                do {
                    let items: [SHMatchedMediaItem] = try await withCheckedThrowingContinuation { cont in
                        delegate.continuation = cont
                        session.match(signature)
                    }
                    if let best = items.first {
                        addOccurrence(from: best, queryTime: t)
                        matchedThisWindow = true
                        break
                    }
                } catch {
                    logLine("[shazamkit] match error @ \(Self.formatTimestamp(t)) (\(Int(win))s): \(error)\n")
                }
            }

            if matchedThisWindow {
                noMatchStreak = 0
                currentStride = defaultStride
                for off in extraProbeOffsets {
                    schedule(t + off)
                }
            } else {
                logLine("[shazamkit] no match @ \(Self.formatTimestamp(t))\n")
                noMatchStreak += 1
                if noMatchStreak >= 2 {
                    currentStride = max(minStride, currentStride / 2)
                }
                if t >= nextRegularTime {
                    nextRegularTime = t + currentStride
                } else if t + currentStride < nextRegularTime {
                    schedule(t + currentStride)
                }
            }

            // Gentle pacing to avoid hammering the service.
            try? await Task.sleep(nanoseconds: 150_000_000) // 150ms
        }

        // Sort by estimated start time if available; fall back to query time.
        return occ.sorted(by: { ($0.estimatedTrackStartSeconds ?? $0.queryTimeSeconds) < ($1.estimatedTrackStartSeconds ?? $1.queryTimeSeconds) })
    }

    func dedupeOccurrences(_ occ: [TrackOccurrence]) -> [TrackOccurrence] {
        // Unique by: ISRC > ShazamID > normalized title+artist
        func norm(_ s: String?) -> String {
            (s ?? "")
                .lowercased()
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        }
        func key(_ o: TrackOccurrence) -> String {
            if let isrc = o.isrc?.trimmingCharacters(in: .whitespacesAndNewlines), !isrc.isEmpty {
                return "isrc:\(isrc.uppercased())"
            }
            if let sid = o.shazamID?.trimmingCharacters(in: .whitespacesAndNewlines), !sid.isEmpty {
                return "shazam:\(sid)"
            }
            return "ta:\(norm(o.title))|\(norm(o.artist))"
        }

        // Keep the best match for each key (prefer higher confidence when available; otherwise earliest).
        var best: [String: TrackOccurrence] = [:]
        for o in occ {
            let k = key(o)
            let t = o.estimatedTrackStartSeconds ?? o.queryTimeSeconds
            if let prev = best[k] {
                let pt = prev.estimatedTrackStartSeconds ?? prev.queryTimeSeconds
                let c = o.confidence ?? 0
                let pc = prev.confidence ?? 0
                if c > pc + 0.05 {
                    best[k] = o
                } else if abs(c - pc) <= 0.05, t < pt {
                    best[k] = o
                }
            } else {
                best[k] = o
            }
        }
        return best.values.sorted(by: { ($0.estimatedTrackStartSeconds ?? $0.queryTimeSeconds) < ($1.estimatedTrackStartSeconds ?? $1.queryTimeSeconds) })
    }

    func cleanupRecognizeWorkDir(workDir: URL, keepFileNames: Set<String>) {
        do {
            let urls = try FileManager.default.contentsOfDirectory(at: workDir, includingPropertiesForKeys: [.isRegularFileKey])
            var removedBytes: Int64 = 0
            var removedCount = 0
            for u in urls {
                let name = u.lastPathComponent
                guard !keepFileNames.contains(name) else { continue }
                guard (try? u.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else { continue }
                let size = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize).map { Int64($0) } ?? 0
                try? FileManager.default.removeItem(at: u)
                removedBytes += size
                removedCount += 1
            }
            if removedCount > 0 {
                artifactsStatusText = "Cleaned up \(removedCount) files (freed ~\(Int(removedBytes / 1_000_000)) MB)."
            } else {
                artifactsStatusText = "Cleanup: nothing to remove."
            }
            // Hide audio output path since it no longer exists.
            outputPath = nil
        } catch {
            artifactsStatusText = "Cleanup failed: \(error)"
        }
    }
}

// MARK: - Telegram

private extension RecognizeModel {
    func telegramClampMessage(_ s: String) -> String {
        // Telegram sendMessage max is 4096 chars; keep some headroom.
        let limit = 3900
        if s.count <= limit { return s }
        return String(s.prefix(limit)) + "\n\n(truncated)\n"
    }

    func formatTelegramMessage(url: String, tracks: [RecognizedTrack], occurrences: [TrackOccurrence]) -> String {
        let cleanURL = url.trimmingCharacters(in: .whitespacesAndNewlines)
        var lines: [String] = []
        lines.append("dj-tools Recognize")
        if !cleanURL.isEmpty { lines.append(cleanURL) }
        lines.append("")

        if tracks.isEmpty, occurrences.isEmpty {
            lines.append("No matches.")
            return lines.joined(separator: "\n")
        }

        if !occurrences.isEmpty {
            lines.append("Matches (mix scan):")
            for (i, o) in occurrences.prefix(12).enumerated() {
                let ts = Self.formatTimestamp(o.estimatedTrackStartSeconds ?? o.queryTimeSeconds)
                let artist = o.artist.map { " — \($0)" } ?? ""
                lines.append("\(i + 1). [\(ts)] \(o.title)\(artist)")
                if let link = o.webURL ?? o.appleMusicURL {
                    lines.append(link)
                }
                lines.append("")
            }
        } else {
            lines.append("Matches:")
            for (i, t) in tracks.prefix(10).enumerated() {
                let artist = t.artist.map { " — \($0)" } ?? ""
                lines.append("\(i + 1). \(t.title)\(artist)")
                if let link = t.webURL ?? t.appleMusicURL {
                    lines.append(link)
                }
                lines.append("")
            }
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
    }

    func telegramSendMessage(token: String, chatID: String, text: String) async throws {
        let url = URL(string: "https://api.telegram.org/bot\(token)/sendMessage")!
        var lastError: Error? = nil

        struct Payload: Encodable {
            let chat_id: String
            let text: String
            let disable_web_page_preview: Bool
        }

        let clampedText = telegramClampMessage(text)
        let body = try JSONEncoder().encode(Payload(chat_id: chatID, text: clampedText, disable_web_page_preview: true))

        // Retry a few times for transient connectivity errors.
        for attempt in 1...3 {
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body

            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                guard let http = resp as? HTTPURLResponse else {
                    throw NSError(domain: "djtools.telegram", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid HTTP response"])
                }
                guard (200..<300).contains(http.statusCode) else {
                    let body = String(data: data, encoding: .utf8) ?? "(non-utf8 body)"
                    if http.statusCode == 429 {
                        // Telegram rate limit: try to honor retry_after if present.
                        if let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                           let params = obj["parameters"] as? [String: Any],
                           let retry = params["retry_after"] as? Int,
                           retry > 0,
                           attempt < 3
                        {
                            try? await Task.sleep(nanoseconds: UInt64(retry + 1) * 1_000_000_000)
                            continue
                        }
                    }
                    throw NSError(domain: "djtools.telegram", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode): \(body)"])
                }
                return
            } catch {
                lastError = error
                // Only retry for URLError-ish failures.
                let ns = error as NSError
                let isRetryable = ns.domain == NSURLErrorDomain
                if attempt < 3, isRetryable {
                    let backoffMs = 250 * attempt * attempt
                    try? await Task.sleep(nanoseconds: UInt64(backoffMs) * 1_000_000)
                    continue
                }
                break
            }
        }
        throw lastError ?? NSError(domain: "djtools.telegram", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unknown error"])
    }
}


