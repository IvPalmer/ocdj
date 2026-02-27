import SwiftUI
import AppKit

struct RecognizeView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var recognizeModel: RecognizeModel
    @State private var isPlaylistTelegramSheetPresented: Bool = false
    @State private var playlistTelegramChatID: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paste a URL. We’ll scan the mix and build a timestamped tracklist.")
                .foregroundStyle(.secondary)

            if appModel.repoRoot == nil {
                Text("Repo root not set. Go to Settings and select the dj-tools folder.")
                    .foregroundStyle(.secondary)
            }

            HStack {
                TextField("Paste a YouTube or SoundCloud URL…", text: $recognizeModel.urlText)
                    .textFieldStyle(.roundedBorder)
                Button("Download + match") {
                    guard let repo = appModel.repoRoot else { return }
                    recognizeModel.startDownload(repo: repo, config: appModel.config, jobsStore: appModel.jobsStore)
                }
                .disabled(
                    recognizeModel.isRunning
                    || appModel.repoRoot == nil
                    || recognizeModel.urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                )
            }

            if recognizeModel.isRunning {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(recognizeModel.stepText ?? "Working…")
                        .foregroundStyle(.secondary)
                }
                if let cur = recognizeModel.scanProgressSeconds,
                   let total = recognizeModel.scanTotalSeconds,
                   total > 0 {
                    ProgressView(value: min(1.0, max(0.0, cur / total))) {
                        Text("Scan progress: \(RecognizeModel.formatTimestamp(cur)) / \(RecognizeModel.formatTimestamp(total))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let errorText = recognizeModel.errorText {
                Text(errorText)
                    .foregroundStyle(.red)
            }

            HStack(spacing: 12) {
                if let n = appModel.config?.recognizeConcurrentFragments {
                    Text("yt-dlp concurrent fragments: \(min(32, max(1, n)))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let jobID = recognizeModel.currentJobID,
                   let job = appModel.jobsStore.jobs.first(where: { $0.id == jobID }) {
                    Text("Job: \(job.status.rawValue)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            HStack(spacing: 10) {
                Button("Open run folder") { openRunFolder() }
                    .disabled(recognizeModel.matchesPath == nil)
                Button("Open matches") { openMatches() }
                    .disabled(recognizeModel.matchesPath == nil)
                if recognizeModel.isRunning {
                    Button("Cancel") { recognizeModel.cancel() }
                }

                Spacer()

                Button("Create Spotify playlist") {
                    Task {
                        let ts = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
                        let name = "dj-tools \(ts)"

                        await appModel.spotifyCreatePlaylist(
                            trackQueries: recognizeModel.occurrences.map { (title: $0.title, artist: $0.artist, isrc: $0.isrc) },
                            name: name
                        )
                    }
                }
                .disabled(recognizeModel.isRunning || (recognizeModel.occurrences.isEmpty && recognizeModel.recognizedTracks.isEmpty))

                Button("Append to Soulseek wanted.txt") {
                    Task {
                        // Format as "track: Artist - Title" (compatible with tools/soulseek_sync/run.py)
                        let rawLines: [String] = recognizeModel.occurrences.map { o in
                            let artist = (o.artist ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                            if artist.isEmpty { return "track: \(o.title)" }
                            return "track: \(artist) - \(o.title)"
                        }

                        // De-dupe locally (preserve order)
                        var seen = Set<String>()
                        let lines = rawLines.filter { seen.insert($0).inserted }

                        await appModel.soulseekAppendWanted(lines: lines)
                    }
                }
                .disabled(recognizeModel.isRunning || (recognizeModel.occurrences.isEmpty && recognizeModel.recognizedTracks.isEmpty))

                Button(recognizeModel.telegramIsSending ? "Sending…" : "Send to Telegram") {
                    Task { await recognizeModel.sendMatchesToTelegram(config: appModel.config) }
                }
                .disabled(recognizeModel.telegramIsSending || recognizeModel.isRunning)
            }

            VStack(alignment: .leading, spacing: 2) {
                if let s = appModel.spotifyStatusText {
                    Text(s).font(.caption).foregroundStyle(.secondary)
                }
                if let url = appModel.spotifyPlaylistURL {
                    HStack(spacing: 10) {
                        Link("Open Spotify playlist", destination: url).font(.caption)
                        Button("Share via Telegram app") {
                            sharePlaylistInTelegramApp(playlistURL: url)
                        }
                        .font(.caption)
                        Button("Send to @deezload2bot (auto)") {
                            Task {
                                let text = "Spotify playlist:\n\(url.absoluteString)"
                                do {
                                    try await appModel.telegramUserSendToBotUsername("@deezload2bot", text: text)
                                    recognizeModel.telegramStatusText = "Sent to @deezload2bot via Telegram user session."
                                } catch {
                                    recognizeModel.telegramStatusText = "Auto-send failed: \(error)"
                                }
                            }
                        }
                        .font(.caption)
                        .disabled(!appModel.telegramUserIsReady)
                        Button(recognizeModel.telegramIsSending ? "Sending…" : "Send playlist to Telegram…") {
                            playlistTelegramChatID = appModel.config?.telegramChatID ?? ""
                            isPlaylistTelegramSheetPresented = true
                        }
                        .font(.caption)
                        .disabled(recognizeModel.telegramIsSending || recognizeModel.isRunning)
                    }
                }
                if let s = appModel.soulseekWantedStatusText {
                    Text(s).font(.caption).foregroundStyle(.secondary)
                }
                if let s = recognizeModel.telegramStatusText {
                    Text(s).font(.caption).foregroundStyle(.secondary)
                }
                if let s = recognizeModel.artifactsStatusText {
                    Text(s).font(.caption).foregroundStyle(.secondary)
                }
            }

            if !recognizeModel.occurrences.isEmpty {
                Divider()
                Text("Matches (\(recognizeModel.occurrences.count))")
                    .font(.headline)

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(recognizeModel.occurrences) { o in
                            VStack(alignment: .leading, spacing: 2) {
                                let ts = RecognizeModel.formatTimestamp(o.estimatedTrackStartSeconds ?? o.queryTimeSeconds)
                                Text("[\(ts)] \(o.title)")
                                    .font(.system(.body, design: .default).weight(.semibold))
                                if let artist = o.artist {
                                    Text(artist)
                                        .foregroundStyle(.secondary)
                                }
                                if let link = o.webURL ?? o.appleMusicURL, let url = URL(string: link) {
                                    Link(link, destination: url)
                                        .font(.caption)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    .padding(.leading, 4)
                    .padding(.trailing, 8)
                }
                .frame(maxWidth: .infinity, maxHeight: 340)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))
            } else if recognizeModel.matchesPath != nil, !recognizeModel.isRunning, recognizeModel.errorText == nil {
                Divider()
                Text("Matches")
                    .font(.headline)
                Text("No matches found.")
                    .foregroundStyle(.secondary)
            }

            Divider()

            Text("Live output")
                .font(.headline)
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(recognizeModel.liveOutput)
                            .font(.system(.body, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                        Text(" ")
                            .id("BOTTOM")
                    }
                    .padding(8)
                }
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))
                .frame(maxWidth: .infinity, minHeight: 220, idealHeight: 280, maxHeight: 320)
                .onChange(of: recognizeModel.liveOutput) { _, _ in
                    // Schedule scroll on the next run loop to avoid "Publishing changes from within view updates"
                    // warnings/crashes in Debug builds when output is very chatty.
                    DispatchQueue.main.async {
                        proxy.scrollTo("BOTTOM", anchor: .bottom)
                    }
                }
            }
        }
        .padding(16)
        // NavigationSplitView detail can vertically-center content if it doesn't claim full height.
        // Force top alignment to avoid the big empty gap above.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .onAppear {
            if let repo = appModel.repoRoot {
                recognizeModel.restoreLastResultsIfAny(repo: repo)
            }
        }
        .sheet(isPresented: $isPlaylistTelegramSheetPresented) {
            PlaylistTelegramSheet(
                chatID: $playlistTelegramChatID,
                onSend: {
                    guard let url = appModel.spotifyPlaylistURL else { return }
                    Task {
                        await recognizeModel.sendSpotifyPlaylistToTelegram(
                            config: appModel.config,
                            playlistURL: url,
                            chatIDOverride: playlistTelegramChatID
                        )
                    }
                }
            )
            .frame(minWidth: 420, idealWidth: 460, minHeight: 160, idealHeight: 180)
            .padding(16)
        }
    }

    private func openRunFolder() {
        guard let p = recognizeModel.matchesPath else { return }
        let runDir = URL(fileURLWithPath: p).deletingLastPathComponent()
        NSWorkspace.shared.open(runDir)
    }

    private func openMatches() {
        guard let p = recognizeModel.matchesPath else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)])
    }

    private func sharePlaylistInTelegramApp(playlistURL: URL) {
        // Bot API cannot send to another bot (bot→bot). For “send to @somebot”, the best UX is
        // opening Telegram’s share UI with the link prefilled so the user can pick the chat/bot.
        let text = "Spotify playlist:\n\(playlistURL.absoluteString)"
        let encodedURL = playlistURL.absoluteString.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? playlistURL.absoluteString
        let encodedText = text.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? text

        // Prefer Telegram URL scheme if available; fall back to web share.
        if let tg = URL(string: "tg://msg_url?url=\(encodedURL)&text=\(encodedText)") {
            NSWorkspace.shared.open(tg)
            return
        }
        if let web = URL(string: "https://t.me/share/url?url=\(encodedURL)&text=\(encodedText)") {
            NSWorkspace.shared.open(web)
        }
    }

}

private struct PlaylistTelegramSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var chatID: String
    let onSend: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Send Spotify playlist to Telegram")
                .font(.headline)
            Text("Enter a numeric chat id (e.g. 123… or -100…) or a channel/supergroup @username. Leave as-is to use your configured default.")
                .foregroundStyle(.secondary)
            Text("Note: bots can’t DM a person just from @username — they must message the bot first, then you use their numeric chat_id.")
                .font(.caption)
                .foregroundStyle(.secondary)

            TextField("Telegram chat id or @username", text: $chatID)
                .textFieldStyle(.roundedBorder)

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Send") {
                    onSend()
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }
}


