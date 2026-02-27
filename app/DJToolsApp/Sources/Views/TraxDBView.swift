import SwiftUI
import AppKit

struct TraxDBView: View {
    @EnvironmentObject private var appModel: AppModel
    @StateObject private var runner = ProcessRunner()

    @State private var maxPages: Int = 50
    @State private var isRunning: Bool = false
    @State private var lastJob: Job? = nil
    @State private var errorText: String? = nil
    @State private var report: TraxDBSyncReport? = nil
    @State private var reportErrorText: String? = nil
    @State private var showOnlyNew: Bool = true
    @State private var showAdvanced: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("TraxDB → Pixeldrain")
                .font(.title2.weight(.semibold))

            if appModel.repoRoot == nil {
                Text("Repo root not set. Go to Settings and select the dj-tools folder.")
                    .foregroundStyle(.secondary)
            }

            traxdbConfigSection()
            traxdbStatusSection()

            HStack(spacing: 12) {
                Stepper("Max pages: \(maxPages)", value: $maxPages, in: 1...500)
                    .frame(width: 220)

                Button("Generate report") { Task { await runSync() } }
                    .disabled(isRunning || appModel.repoRoot == nil || !isTraxDBConfigured)

                Button("Download (background)") { Task { await runDownloadBg() } }
                    .disabled(isRunning || appModel.repoRoot == nil || !isPixeldrainConfigured)
            }
            .buttonStyle(.bordered)

            Text("Recommended: Generate report → Download (background).")
                .font(.caption)
                .foregroundStyle(.secondary)

            DisclosureGroup("Advanced", isExpanded: $showAdvanced) {
                HStack(spacing: 12) {
                    Button("Audit") { Task { await runAudit() } }
                        .disabled(isRunning || appModel.repoRoot == nil)
                    Button("Open report file") { openReportFile() }
                        .disabled(reportURL() == nil)
                }
                .buttonStyle(.bordered)
                .padding(.top, 4)
            }

            if let errorText {
                Text(errorText).foregroundStyle(.red)
            }

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
        .onAppear {
            loadReport()
        }
    }

    private var isTraxDBConfigured: Bool {
        let start = (appModel.config?.traxdbStartURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let cookies = (appModel.config?.traxdbCookies ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if start.isEmpty { return false }
        // Cookies are optional unless the user explicitly marks TraxDB as private.
        let requiresCookies = appModel.config?.traxdbRequiresCookies ?? false
        if requiresCookies {
            return !cookies.isEmpty && FileManager.default.fileExists(atPath: cookies)
        }
        if cookies.isEmpty { return true }
        return FileManager.default.fileExists(atPath: cookies)
    }

    private var isPixeldrainConfigured: Bool {
        let key = (appModel.config?.pixeldrainAPIKey ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return !key.isEmpty
    }

    @ViewBuilder
    private func traxdbConfigSection() -> some View {
        let start = (appModel.config?.traxdbStartURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let cookies = (appModel.config?.traxdbCookies ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let cookiesExists = !cookies.isEmpty && FileManager.default.fileExists(atPath: cookies)
        let requiresCookies = appModel.config?.traxdbRequiresCookies ?? false
        let repoCookiesPath = appModel.repoRoot?
            .appendingPathComponent("tools/traxdb_sync/traxdb_cookies.txt", isDirectory: false)
            .path
        let repoCookiesExists = repoCookiesPath.map { FileManager.default.fileExists(atPath: $0) } ?? false
        let id3 = effectiveID3Root()

        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text("Config")
                    .font(.headline)
                Spacer()
                Button("Open djtools_config.json") { appModel.openConfigFile() }
                    .font(.caption)
            }

            HStack(spacing: 10) {
                Text("ID3 root:")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(id3 ?? "(not set)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(id3 == nil ? .red : .secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button("Choose…") { pickID3Root() }
                    .font(.caption)
                if let id3, !id3.isEmpty {
                    Button("Open") { NSWorkspace.shared.open(URL(fileURLWithPath: id3, isDirectory: true)) }
                        .font(.caption)
                }
            }

            if start.isEmpty {
                Text("Missing traxdb_start_url in djtools_config.json")
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            if cookies.isEmpty || !cookiesExists {
                Text(cookies.isEmpty ? "TraxDB cookies: (not set)" : "TraxDB cookies file not found: \(cookies)")
                    .font(.caption)
                    .foregroundStyle(requiresCookies ? .red : .secondary)
                if requiresCookies {
                    Text("This TraxDB source is marked as private, so cookies are required.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Cookies are optional — only needed if the blog is private / rate-limited.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 10) {
                    Button("Pick TraxDB cookies file…") { pickCookiesFile() }
                    if repoCookiesExists, let repoCookiesPath {
                        Button("Use repo cookies file") {
                            appModel.updateConfigValue(key: "traxdb_cookies", value: repoCookiesPath)
                        }
                    }
                    if !cookies.isEmpty {
                        Button("Clear") {
                            appModel.updateConfigValue(key: "traxdb_cookies", value: "")
                        }
                    }
                }
            } else {
                Text("TraxDB cookies: \(cookies)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !isPixeldrainConfigured {
                Text("Pixeldrain API key is missing (downloads will be disabled).")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.35))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.20)))
        .cornerRadius(8)
    }

    @ViewBuilder
    private func traxdbStatusSection() -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Status")
                    .font(.headline)
                Spacer()
                Toggle("Only new", isOn: $showOnlyNew)
                    .toggleStyle(.switch)
                    .font(.caption)
                Button("Reload") { loadReport() }
                    .font(.caption)
            }

            if let reportErrorText {
                Text(reportErrorText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let report {
                let newCount = report.linksNew?.count ?? 0
                let totalCount = report.linksFound?.count ?? 0
                let newDateFolders = computeNewDateFolders(report: report)
                HStack(spacing: 14) {
                    Text("New lists: \(newCount)")
                        .font(.caption)
                        .foregroundStyle(newCount > 0 ? .primary : .secondary)
                    Text("New date folders: \(newDateFolders.count)")
                        .font(.caption)
                        .foregroundStyle(newDateFolders.count > 0 ? .primary : .secondary)
                    Text("Total lists seen: \(totalCount)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                }

                Divider().opacity(0.35)

                let rows = (showOnlyNew ? (report.linksNew ?? []) : (report.linksFound ?? []))
                if rows.isEmpty {
                    Text(showOnlyNew ? "No new lists found." : "No lists in report.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(rows) { l in
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack(spacing: 8) {
                                        Text(l.inferredDate ?? "(no date)")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .frame(width: 92, alignment: .leading)
                                        Text(l.listID)
                                            .font(.system(.caption, design: .monospaced))
                                            .foregroundStyle(.secondary)
                                        Spacer()
                                        if let url = URL(string: l.pixeldrainURL) {
                                            Link("Pixeldrain", destination: url)
                                                .font(.caption)
                                        }
                                        if let src = l.sourceURL, let srcURL = URL(string: src) {
                                            Link("Source", destination: srcURL)
                                                .font(.caption)
                                        }
                                    }
                                }
                            }
                        }
                        .padding(.vertical, 6)
                    }
                    .frame(maxHeight: 220)
                    .background(Color(nsColor: .textBackgroundColor).opacity(0.35))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.20)))
                }
            } else {
                Text("No report loaded yet. Click “Generate report”, then it will show up here.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.35))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.20)))
        .cornerRadius(8)
    }

    private func pickCookiesFile() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.title = "Select TraxDB cookies file"
        panel.prompt = "Select"
        panel.allowedContentTypes = [.json, .plainText]
        if let repo = appModel.repoRoot {
            panel.directoryURL = repo.appendingPathComponent("tools/traxdb_sync", isDirectory: true)
        }
        if panel.runModal() == .OK, let url = panel.url {
            appModel.updateConfigValue(key: "traxdb_cookies", value: url.path)
        }
    }

    private func pickID3Root() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.title = "Select ID3 root folder"
        panel.prompt = "Select"
        if let existing = effectiveID3Root() {
            panel.directoryURL = URL(fileURLWithPath: existing, isDirectory: true)
        }
        if panel.runModal() == .OK, let url = panel.url {
            appModel.updateConfigValue(key: "djtools_id3_root", value: url.path)
        }
    }

    private func runSync() async {
        await runScript(
            title: "TraxDB: generate report",
            scriptRel: "tools/traxdb_sync/run_sync.sh",
            args: ["--max-pages", "\(maxPages)"]
        )
        loadReport()
    }

    private func runDownloadBg() async {
        await runScript(
            title: "TraxDB: download background",
            scriptRel: "tools/traxdb_sync/run_download_bg.sh",
            args: []
        )
    }

    private func runAudit() async {
        await runScript(
            title: "TraxDB: audit",
            scriptRel: "tools/traxdb_sync/run_audit.sh",
            args: []
        )
    }

    private func runScript(title: String, scriptRel: String, args: [String]) async {
        guard let repo = appModel.repoRoot else { return }
        isRunning = true
        errorText = nil
        runner.liveOutput = ""

        let logsDir = repo.appendingPathComponent("logs", isDirectory: true)
        let reportPath = logsDir.appendingPathComponent("traxdb_sync_report_links.json", isDirectory: false).path
        let auditPath = logsDir.appendingPathComponent("traxdb_audit_latest.json", isDirectory: false).path
        let ts = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
        let logPath = logsDir.appendingPathComponent("app_\(ts).log").path

        var job = Job(
            title: title,
            commandLine: ["/bin/bash", repo.appendingPathComponent(scriptRel).path] + args,
            workingDirectory: repo.path
        )
        job.logPath = logPath
        job.artifactsPath = logsDir.path
        // These scripts are configured to write to stable paths via env vars below.
        // Expose that in Jobs UI so you can open the report without digging.
        if scriptRel.contains("run_sync.sh") {
            job.finalPath = reportPath
        } else if scriptRel.contains("run_audit.sh") {
            job.finalPath = auditPath
        }
        job.status = .running
        job.startedAt = Date()

        appModel.jobsStore.add(job)
        lastJob = job

        do {
            // mirror runner output into a log file as well
            try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: logPath, contents: nil)
            let logHandle = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
            defer { try? logHandle.close() }

            let result = try await runner.run(
                argv: job.commandLine,
                cwd: repo,
                environment: [
                    "DJTOOLS_ARTIFACTS_ROOT": repo.path,
                    "DJTOOLS_ID3_ROOT": effectiveID3Root() ?? "",
                    "DJTOOLS_TRAXDB_REPORT_PATH": reportPath,
                    "DJTOOLS_TRAXDB_AUDIT_PATH": auditPath,
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

    private func effectiveID3Root() -> String? {
        let explicit = (appModel.config?.djtoolsID3Root ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !explicit.isEmpty { return explicit }
        // Heuristic: if soulseek_root is ".../ID3/soulseek", derive ".../ID3".
        if let sr = appModel.config?.soulseekRoot, !sr.isEmpty {
            let u = URL(fileURLWithPath: sr)
            let parent = u.deletingLastPathComponent().path
            if u.lastPathComponent.lowercased() == "soulseek" {
                return parent
            }
        }
        return nil
    }

    private func reportURL() -> URL? {
        guard let repo = appModel.repoRoot else { return nil }
        return repo.appendingPathComponent("logs/traxdb_sync_report_links.json", isDirectory: false)
    }

    private func openReportFile() {
        guard let url = reportURL() else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    private func loadReport() {
        reportErrorText = nil
        guard let url = reportURL() else {
            report = nil
            reportErrorText = "Repo root not set."
            return
        }
        guard FileManager.default.fileExists(atPath: url.path) else {
            report = nil
            reportErrorText = "Report not found yet: \(url.path)"
            return
        }
        do {
            let data = try Data(contentsOf: url)
            let decoder = JSONDecoder()
            report = try decoder.decode(TraxDBSyncReport.self, from: data)
        } catch {
            report = nil
            reportErrorText = "Failed to load report: \(error)"
        }
    }

    private func computeNewDateFolders(report: TraxDBSyncReport) -> [String] {
        guard let id3 = effectiveID3Root() else { return [] }
        let traxdbRoot = URL(fileURLWithPath: id3, isDirectory: true).appendingPathComponent("traxdb", isDirectory: true).path
        let existing = (try? FileManager.default.contentsOfDirectory(atPath: traxdbRoot)) ?? []
        let existingSet = Set(existing)
        let dates = (report.linksNew ?? [])
            .compactMap { $0.inferredDate }
            .filter { !$0.isEmpty }
        let uniq = Array(Set(dates)).sorted()
        return uniq.filter { !existingSet.contains($0) }
    }
}

// MARK: - TraxDB report decoding (for in-app status UI)

private struct TraxDBSyncReport: Decodable {
    var generatedAt: String?
    var traxdbRoot: String?
    var linksFound: [TraxDBLink]?
    var linksNew: [TraxDBLink]?
    var errors: [TraxDBReportError]?

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case traxdbRoot = "traxdb_root"
        case linksFound = "links_found"
        case linksNew = "links_new"
        case errors
    }
}

private struct TraxDBReportError: Decodable {
    var listID: String?
    var pixeldrainURL: String?
    var sourceURL: String?
    var error: String?

    enum CodingKeys: String, CodingKey {
        case listID = "list_id"
        case pixeldrainURL = "pixeldrain_url"
        case sourceURL = "source_url"
        case error
    }
}

private struct TraxDBLink: Identifiable, Decodable {
    var id: String { listID }
    var listID: String
    var pixeldrainURL: String
    var sourceURL: String?
    var inferredDate: String?

    enum CodingKeys: String, CodingKey {
        case listID = "list_id"
        case pixeldrainURL = "pixeldrain_url"
        case sourceURL = "source_url"
        case inferredDate = "inferred_date"
    }
}


