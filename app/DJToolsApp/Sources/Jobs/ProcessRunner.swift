import Foundation

struct ProcessResult {
    var exitCode: Int32
}

@MainActor
final class ProcessRunner: ObservableObject {
    enum RunnerError: Error, CustomStringConvertible {
        case invalidCommand
        case startFailed(String)

        var description: String {
            switch self {
            case .invalidCommand:
                return "Invalid command"
            case .startFailed(let msg):
                return msg
            }
        }
    }

    @Published var liveOutput: String = ""

    private var currentProcess: Process? = nil
    private var currentStdout: Pipe? = nil
    private var currentStderr: Pipe? = nil

    func terminate() {
        guard let p = currentProcess else { return }
        if p.isRunning {
            // Prefer a gentle interrupt if possible, then terminate.
            p.interrupt()
            p.terminate()
        }
    }

    func run(
        argv: [String],
        cwd: URL?,
        environment: [String: String] = [:],
        onLine: ((String) -> Void)? = nil
    ) async throws -> ProcessResult {
        guard let exe = argv.first, argv.count >= 1 else { throw RunnerError.invalidCommand }
        let args = Array(argv.dropFirst())

        // Only one running process at a time per runner instance.
        if let p = currentProcess, p.isRunning {
            throw RunnerError.startFailed("A process is already running.")
        }

        // Reset output for a fresh run.
        liveOutput = ""

        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        if let cwd { p.currentDirectoryURL = cwd }

        var env = ProcessInfo.processInfo.environment
        for (k, v) in environment { env[k] = v }
        p.environment = env

        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe

        currentProcess = p
        currentStdout = outPipe
        currentStderr = errPipe

        let maxLiveOutputChars = 200_000
        func append(_ s: String) {
            liveOutput.append(s)
            // Cap output size to prevent memory/rendering issues on long-running processes.
            if liveOutput.count > maxLiveOutputChars {
                liveOutput = String(liveOutput.suffix(maxLiveOutputChars))
            }
            onLine?(s)
        }

        outPipe.fileHandleForReading.readabilityHandler = { h in
            let data = h.availableData
            guard !data.isEmpty else { return }
            if let s = String(data: data, encoding: .utf8) {
                Task { @MainActor in append(s) }
            }
        }
        errPipe.fileHandleForReading.readabilityHandler = { h in
            let data = h.availableData
            guard !data.isEmpty else { return }
            if let s = String(data: data, encoding: .utf8) {
                Task { @MainActor in append(s) }
            }
        }

        do {
            try p.run()
        } catch {
            currentProcess = nil
            currentStdout = nil
            currentStderr = nil
            throw RunnerError.startFailed("Failed to start process: \(error)")
        }

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { cont in
                p.terminationHandler = { [weak self] proc in
                    Task { @MainActor in
                        outPipe.fileHandleForReading.readabilityHandler = nil
                        errPipe.fileHandleForReading.readabilityHandler = nil
                        self?.currentProcess = nil
                        self?.currentStdout = nil
                        self?.currentStderr = nil
                        cont.resume(returning: ProcessResult(exitCode: proc.terminationStatus))
                    }
                }
            }
        } onCancel: {
            Task { @MainActor in
                self.terminate()
            }
        }
    }
}


