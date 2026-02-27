import Foundation

enum JobStatus: String, Codable {
    case queued
    case running
    case succeeded
    case failed
    case cancelled
}

struct Job: Identifiable, Codable, Hashable {
    var id: UUID
    var createdAt: Date
    var startedAt: Date?
    var endedAt: Date?

    var title: String
    var commandLine: [String]   // argv
    var workingDirectory: String?

    var status: JobStatus
    var exitCode: Int32?

    var logPath: String?
    var progressPath: String?
    var finalPath: String?
    var artifactsPath: String?

    var errorMessage: String?

    init(
        id: UUID = UUID(),
        createdAt: Date = Date(),
        title: String,
        commandLine: [String],
        workingDirectory: String? = nil,
        status: JobStatus = .queued
    ) {
        self.id = id
        self.createdAt = createdAt
        self.title = title
        self.commandLine = commandLine
        self.workingDirectory = workingDirectory
        self.status = status
    }
}


