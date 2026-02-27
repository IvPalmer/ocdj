import SwiftUI
import AppKit

struct SoulseekView: View {
    @EnvironmentObject private var appModel: AppModel
    @StateObject private var runner = ProcessRunner()

    @State private var isRunning: Bool = false
    @State private var lastJob: Job? = nil
    @State private var errorText: String? = nil
    @State private var wantedText: String = ""
    @State private var wantedStatusText: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Soulseek (slskd)")
                .font(.title2.weight(.semibold))

            if appModel.repoRoot == nil {
                Text("Repo root not set. Go to Settings and select the dj-tools folder.")
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 12) {
                Button("Run wanted.txt (background)") { Task { await runBg() } }
                    .disabled(isRunning || appModel.repoRoot == nil)
                Button("Status") { Task { await runStatus() } }
                    .disabled(isRunning || appModel.repoRoot == nil)
                if let base = appModel.config?.slskdBaseURL, let url = URL(string: base) {
                    Button("Open slskd UI") { NSWorkspace.shared.open(url) }
                        .disabled(isRunning)
                }
            }
            .buttonStyle(.bordered)

            if let errorText {
                Text(errorText).foregroundStyle(.red)
            }
            if let wantedStatusText {
                Text(wantedStatusText).font(.caption).foregroundStyle(.secondary)
            }

            Divider()
            Text("wanted.txt")
                .font(.headline)

            HStack(spacing: 10) {
                Button("Reload") { loadWanted() }
                    .disabled(appModel.repoRoot == nil)
                Button("Save") { saveWanted() }
                    .disabled(appModel.repoRoot == nil)
                Spacer()
                if let repo = appModel.repoRoot {
                    Button("Open wanted.txt") {
                        let url = repo.appendingPathComponent("tools/soulseek_sync/wanted.txt")
                        NSWorkspace.shared.open(url)
                    }
                }
            }
            .buttonStyle(.bordered)

            TextEditor(text: $wantedText)
                .font(.system(.body, design: .monospaced))
                .frame(maxWidth: .infinity, maxHeight: 240)
                .background(Color(nsColor: .textBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))

            if let job = lastJob {
                Divider()
                Text("Latest job: \(job.title)").font(.headline)
                if let p = job.logPath {
                    Text("Log: \(p)").font(.caption).foregroundStyle(.secondary)
                }
            }

            Divider()

            Text("Live output")
                .font(.headline)
            ScrollView {
                Text(runner.liveOutput)
                    .font(.system(.body, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))

            Spacer()
        }
        .padding(16)
        .onAppear { loadWanted() }
    }

    private func runBg() async {
        await runScript(title: "Soulseek: run wanted (bg)", scriptRel: "tools/soulseek_sync/run_bg.sh", args: [])
    }

    private func runStatus() async {
        await runScript(title: "Soulseek: status", scriptRel: "tools/soulseek_sync/status.sh", args: [])
    }

    private func runScript(title: String, scriptRel: String, args: [String]) async {
        guard let repo = appModel.repoRoot else { return }
        isRunning = true
        errorText = nil
        runner.liveOutput = ""

        let logsDir = repo.appendingPathComponent("logs", isDirectory: true)
        let ts = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
        let logPath = logsDir.appendingPathComponent("app_\(ts).log").path

        var job = Job(
            title: title,
            commandLine: ["/bin/bash", repo.appendingPathComponent(scriptRel).path] + args,
            workingDirectory: repo.path
        )
        job.logPath = logPath
        job.artifactsPath = logsDir.path
        job.status = .running
        job.startedAt = Date()

        appModel.jobsStore.add(job)
        lastJob = job

        do {
            try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: logPath, contents: nil)
            let logHandle = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
            defer { try? logHandle.close() }

            let result = try await runner.run(
                argv: job.commandLine,
                cwd: repo,
                environment: [
                    "DJTOOLS_ARTIFACTS_ROOT": repo.path
                ],
                onLine: { s in
                    if let data = s.data(using: .utf8) {
                        try? logHandle.write(contentsOf: data)
                    }
                }
            )

            job.exitCode = result.exitCode
            job.endedAt = Date()
            job.status = (result.exitCode == 0) ? .succeeded : .failed
            appModel.jobsStore.update(job)
            lastJob = job
        } catch {
            job.endedAt = Date()
            job.status = .failed
            job.errorMessage = String(describing: error)
            appModel.jobsStore.update(job)
            lastJob = job
            errorText = "Failed: \(error)"
        }

        isRunning = false
    }

    private func wantedURL() -> URL? {
        appModel.repoRoot?.appendingPathComponent("tools/soulseek_sync/wanted.txt")
    }

    private func loadWanted() {
        wantedStatusText = nil
        guard let url = wantedURL() else { return }
        let data = (try? Data(contentsOf: url)) ?? Data()
        wantedText = String(data: data, encoding: .utf8) ?? ""
        wantedStatusText = "Loaded wanted.txt"
    }

    private func saveWanted() {
        wantedStatusText = nil
        guard let url = wantedURL() else { return }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let s = wantedText.hasSuffix("\n") ? wantedText : wantedText + "\n"
            try s.data(using: .utf8)?.write(to: url, options: [.atomic])
            wantedStatusText = "Saved wanted.txt"
        } catch {
            wantedStatusText = "Save failed: \(error)"
        }
    }
}


