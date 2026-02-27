import Foundation

@MainActor
final class JobsStore: ObservableObject {
    @Published private(set) var jobs: [Job] = []

    private var repoRoot: URL? = nil
    private var persistenceURL: URL? = nil

    func setRepoRoot(_ url: URL?) {
        repoRoot = url
        if let url {
            persistenceURL = url.appendingPathComponent("logs/jobs.json", isDirectory: false)
            load()
        } else {
            persistenceURL = nil
            jobs = []
        }
    }

    func add(_ job: Job) {
        jobs.insert(job, at: 0)
        save()
    }

    func update(_ job: Job) {
        if let idx = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[idx] = job
            save()
        }
    }

    func clear() {
        jobs = []
        save()
    }

    private func load() {
        guard let url = persistenceURL else { return }
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder.withISO8601Dates.decode([Job].self, from: data)
            jobs = decoded
        } catch {
            // If file doesn't exist or is invalid, start fresh.
            jobs = []
        }
    }

    private func save() {
        guard let url = persistenceURL else { return }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let data = try JSONEncoder.withISO8601Dates.encode(jobs)
            try data.write(to: url, options: [.atomic])
        } catch {
            // Non-fatal: persistence failure shouldn't break UI.
        }
    }
}

private extension JSONDecoder {
    static var withISO8601Dates: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }
}

private extension JSONEncoder {
    static var withISO8601Dates: JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.dateEncodingStrategy = .iso8601
        return e
    }
}


