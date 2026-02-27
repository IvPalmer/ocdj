import SwiftUI
import Foundation

@main
struct DJToolsApp: App {
    private enum LaunchMode {
        case gui
        case smokeRecognize(repo: URL, url: String)
        case smokeMatchFile(wav: URL)
    }

    private let launchMode: LaunchMode
    @StateObject private var appModel: AppModel

    init() {
        self.launchMode = Self.parseLaunchMode()
        _appModel = StateObject(wrappedValue: AppModel())

        if case let .smokeRecognize(repo, url) = launchMode {
            Task { @MainActor in
                // Load config (best-effort) and run the pipeline headlessly.
                let cfgPath = repo.appendingPathComponent("djtools_config.json")
                let cfg: DJToolsConfig? = (try? Data(contentsOf: cfgPath)).flatMap { try? JSONDecoder().decode(DJToolsConfig.self, from: $0) }

                let jobs = JobsStore()
                jobs.setRepoRoot(repo)

                let model = RecognizeModel()
                let ok = await model.runDownloadForCLI(repo: repo, config: cfg, jobsStore: jobs, url: url)
                exit(ok ? 0 : 1)
            }
        } else if case let .smokeMatchFile(wav) = launchMode {
            Task { @MainActor in
                let model = RecognizeModel()
                let ok = await model.runMatchFileForCLI(wavURL: wav)
                exit(ok ? 0 : 1)
            }
        }
    }

    var body: some Scene {
        WindowGroup("dj-tools") {
            switch launchMode {
            case .gui:
                ContentView()
                    .environmentObject(appModel)
                    .environmentObject(appModel.recognizeModel)
                    .onOpenURL { url in
                        appModel.handleIncomingURL(url)
                    }
            case .smokeRecognize:
                // Smoke mode: keep UI minimal; we exit() when the task finishes.
                VStack(alignment: .leading, spacing: 8) {
                    Text("dj-tools smoke mode")
                        .font(.headline)
                    Text("Running Recognize pipeline from command line…")
                        .foregroundStyle(.secondary)
                }
                .padding(16)
            case .smokeMatchFile:
                VStack(alignment: .leading, spacing: 8) {
                    Text("dj-tools smoke mode")
                        .font(.headline)
                    Text("Running ShazamKit match on local wav…")
                        .foregroundStyle(.secondary)
                }
                .padding(16)
            }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Open Logs Folder") {
                    appModel.openLogsFolder()
                }
                .keyboardShortcut("l", modifiers: [.command, .shift])
            }
        }
    }

    private static func parseLaunchMode() -> LaunchMode {
        let args = CommandLine.arguments

        func value(after flag: String) -> String? {
            guard let i = args.firstIndex(of: flag) else { return nil }
            let j = args.index(after: i)
            guard j < args.endIndex else { return nil }
            return args[j]
        }

        if let url = value(after: "--smoke-recognize") {
            let repoStr = value(after: "--repo") ?? ProcessInfo.processInfo.environment["DJTOOLS_REPO_ROOT"]
            let repoURL = repoStr.map { URL(fileURLWithPath: $0) } ?? RepoLocator.findRepoRoot()
            if let repoURL {
                return .smokeRecognize(repo: repoURL, url: url)
            } else {
                // Can't surface UI; print and exit.
                FileHandle.standardError.write(Data("ERROR: repo root not found. Pass --repo /path/to/dj-tools (or set DJTOOLS_REPO_ROOT).\n".utf8))
                exit(2)
            }
        }

        if let wav = value(after: "--smoke-match-file") {
            return .smokeMatchFile(wav: URL(fileURLWithPath: wav))
        }

        return .gui
    }
}


