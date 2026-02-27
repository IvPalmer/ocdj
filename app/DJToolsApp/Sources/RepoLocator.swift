import Foundation

enum RepoLocator {
    static func findRepoRoot() -> URL? {
        // 1) Environment override
        if let env = ProcessInfo.processInfo.environment["DJTOOLS_REPO_ROOT"], !env.isEmpty {
            return URL(fileURLWithPath: env)
        }

        // 2) Try current working directory (works when launched from `swift run` in repo)
        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        if isRepoRoot(cwd) { return cwd }

        // 3) Walk up from CWD
        if let found = walkUp(from: cwd) { return found }

        // 4) Walk up from executable directory (best-effort)
        let execDir = URL(fileURLWithPath: CommandLine.arguments.first ?? ".")
            .deletingLastPathComponent()
        if isRepoRoot(execDir) { return execDir }
        return walkUp(from: execDir)
    }

    private static func walkUp(from start: URL) -> URL? {
        var cur = start
        for _ in 0..<12 {
            if isRepoRoot(cur) { return cur }
            let next = cur.deletingLastPathComponent()
            if next.path == cur.path { break }
            cur = next
        }
        return nil
    }

    private static func isRepoRoot(_ url: URL) -> Bool {
        let fm = FileManager.default
        let config = url.appendingPathComponent("djtools_config.json")
        let tools = url.appendingPathComponent("tools", isDirectory: true)
        return fm.fileExists(atPath: config.path) && fm.fileExists(atPath: tools.path)
    }
}


