import SwiftUI
import AppKit

struct JobsView: View {
    @EnvironmentObject private var appModel: AppModel

    @State private var selectedJobID: UUID? = nil

    /// Look up the live job from the store so status updates are always reflected.
    private var selectedJob: Job? {
        guard let id = selectedJobID else { return nil }
        return appModel.jobsStore.jobs.first(where: { $0.id == id })
    }

    var body: some View {
        HStack(spacing: 0) {
            List(selection: $selectedJobID) {
                ForEach(appModel.jobsStore.jobs) { job in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(job.title).font(.headline)
                            Spacer()
                            Text(job.status.rawValue).foregroundStyle(.secondary)
                        }
                        Text(job.createdAt.formatted(date: .abbreviated, time: .standard))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .tag(job.id as UUID?)
                }
            }
            .frame(minWidth: 280, idealWidth: 320)

            Divider()

            if let job = selectedJob {
                JobDetailView(job: job)
                    .frame(minWidth: 520)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Select a job").font(.headline)
                    Text("Run something from TraxDB / Soulseek / Recognize and it will show up here.")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                .padding(16)
            }
        }
        .onAppear {
            if selectedJobID == nil {
                selectedJobID = appModel.jobsStore.jobs.first?.id
            }
        }
    }
}

private struct JobDetailView: View {
    @EnvironmentObject private var appModel: AppModel
    let job: Job

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(job.title).font(.title2.weight(.semibold))

            LabeledContent("Status", value: job.status.rawValue)
            if let exit = job.exitCode {
                LabeledContent("Exit", value: "\(exit)")
            }
            if let wd = job.workingDirectory {
                LabeledContent("CWD", value: wd)
            }
            LabeledContent("Command", value: job.commandLine.joined(separator: " "))

            Divider()

            HStack {
                if let p = job.logPath { Button("Reveal log") { revealPath(p) } }
                if let p = job.progressPath { Button("Reveal progress") { revealPath(p) } }
                if let p = job.finalPath { Button("Reveal final") { revealPath(p) } }
                if let p = job.artifactsPath { Button("Open folder") { openFolder(p) } }
                Spacer()
            }
            .buttonStyle(.bordered)

            if let p = job.logPath {
                LogTailView(path: p)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text("No log attached to this job.")
                    .foregroundStyle(.secondary)
                Spacer()
            }
        }
        .padding(16)
    }

    private func openFolder(_ p: String) {
        let url = URL(fileURLWithPath: p)
        NSWorkspace.shared.open(url)
    }

    private func revealPath(_ p: String) {
        let url = URL(fileURLWithPath: p)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

private struct LogTailView: View {
    let path: String

    @State private var text: String = ""
    @State private var timer: Timer? = nil

    var body: some View {
        ScrollView {
            Text(text)
                .font(.system(.body, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
        }
        .background(Color(nsColor: .textBackgroundColor))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))
        .onAppear { start() }
        .onDisappear { stop() }
    }

    private func start() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { _ in
            refresh()
        }
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func refresh() {
        if let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
           let s = String(data: data, encoding: .utf8) {
            // Keep it simple: show last ~200 KB to avoid UI blowups.
            let maxBytes = 200_000
            if s.utf8.count > maxBytes {
                text = String(s.suffix(maxBytes))
            } else {
                text = s
            }
        } else {
            text = "(log not found yet) \(path)"
        }
    }
}


