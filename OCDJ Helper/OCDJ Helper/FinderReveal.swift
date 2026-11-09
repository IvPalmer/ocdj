import Foundation
import Darwin

/// Only opaque identifiers enter through LaunchServices. Paths stay in this local sidecar.
struct FinderReveal {
    struct Request {
        let collection: String
        let id: String

        init(_ url: URL) throws {
            let pattern = #"^ocdj-reveal://track/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/([0-9a-f]{64})$"#
            let text = url.absoluteString
            let regex = try NSRegularExpression(pattern: pattern)
            let range = NSRange(text.startIndex..., in: text)
            guard let match = regex.firstMatch(in: text, range: range), match.range == range,
                  let collectionRange = Range(match.range(at: 1), in: text),
                  let idRange = Range(match.range(at: 2), in: text) else { throw Failure.invalidURL }
            collection = String(text[collectionRange])
            id = String(text[idRange])
            guard UUID(uuidString: collection)?.uuidString.lowercased() == collection else { throw Failure.invalidURL }
        }
    }

    struct Map: Decodable {
        let version: Int
        let collection_id: String
        let root: String
        let tracks: [String: String]
    }

    enum Failure: Error {
        case invalidURL, staleMap, staleFile
        var message: String {
            switch self {
            case .invalidURL: return "This OCDJ Finder link is invalid. Refresh the Wanted page."
            case .staleMap: return "The local Finder map is missing or stale. Refresh it with the Mac snapshot tool using the existing collection and key."
            case .staleFile: return "The indexed file is missing, moved, or unsafe to reveal. Refresh the local Finder map."
            }
        }
    }

    static var mapURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("OCDJ/reveal-map.json")
    }

    static func isAbsolutePath(_ path: String) -> Bool {
        path.hasPrefix("/") && !path.contains("\0") && path != "/" &&
            !path.components(separatedBy: "/").dropFirst().contains(where: { $0.isEmpty || $0 == "." || $0 == ".." })
    }

    // Open each component without following symlinks (including ancestor directories).
    static func openVerified(_ path: String, directory: Bool = false) throws -> Int32 {
        guard isAbsolutePath(path) else { throw Failure.staleFile }
        let parts = path.split(separator: "/", omittingEmptySubsequences: true)
        var fd = Darwin.open("/", O_EVTONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else { throw Failure.staleFile }
        for (index, part) in parts.enumerated() {
            let isDirectory = directory || index < parts.count - 1
            let flags = O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC |
                (isDirectory ? O_EVTONLY | O_DIRECTORY : O_RDONLY)
            let next = part.withCString { openat(fd, $0, flags) }
            close(fd)
            guard next >= 0 else { throw Failure.staleFile }
            fd = next
        }
        return fd
    }

    static func load(_ request: Request, from url: URL = mapURL) throws -> Map {
        do {
            let fd = try openVerified(url.path)
            let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            defer { try? handle.close() }
            var info = stat()
            guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
                  info.st_uid == getuid(), info.st_mode & 0o077 == 0, info.st_nlink == 1,
                  info.st_size > 0, info.st_size <= 32 * 1024 * 1024 else { throw Failure.staleMap }
            let data = try handle.read(upToCount: 32 * 1024 * 1024 + 1) ?? Data()
            guard data.count <= 32 * 1024 * 1024 else { throw Failure.staleMap }
            let map = try JSONDecoder().decode(Map.self, from: data)
            guard map.version == 1, map.collection_id == request.collection,
                  map.tracks.count <= 100_000, map.tracks[request.id] != nil,
                  isAbsolutePath(map.root) else { throw Failure.staleMap }
            return map
        } catch { throw Failure.staleMap }
    }

    static func target(_ request: Request, in map: Map) throws -> URL {
        guard map.collection_id == request.collection, let relative = map.tracks[request.id] else { throw Failure.staleMap }
        let parts = relative.components(separatedBy: "/")
        guard !relative.contains("\0"), !parts.isEmpty,
              !parts.contains(where: { $0.isEmpty || $0 == "." || $0 == ".." || ["id3", "traxdb"].contains($0.lowercased()) }) else { throw Failure.staleFile }
        let path = map.root + "/" + relative
        let fd = try openVerified(path)
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else { throw Failure.staleFile }
        return URL(fileURLWithPath: path)
    }
}
