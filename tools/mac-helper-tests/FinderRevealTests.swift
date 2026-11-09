import Foundation
import Darwin

@main
struct FinderRevealTests {
    static func main() throws {
        // This child uses the shipped functions inside an actual synthetic sandbox.
        if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--scoped" {
            let base = URL(fileURLWithPath: CommandLine.arguments[2])
            let denied = Darwin.open(base.path, O_EVTONLY | O_DIRECTORY)
            guard denied < 0, errno == EPERM || errno == EACCES else {
                if denied >= 0 { close(denied) }
                exit(2)
            }
            let collection = "00000000-0000-0000-0000-000000000001"
            let id = String(repeating: "a", count: 64)
            let request = try FinderReveal.Request(URL(string: "ocdj-reveal://track/\(collection)/\(id)")!)
            do {
                let map = try FinderReveal.load(request, from: base.appendingPathComponent("map.json"))
                print("Scoped sandbox: map loaded")
                let target = try FinderReveal.target(request, in: map)
                guard target.path == base.appendingPathComponent("library/fixture.wav").path else { exit(3) }
                print("Scoped sandbox: ancestor open denied; map and target succeeded")
            } catch {
                print("Scoped sandbox: shipped map/target functions refused accessible fixture")
                exit(1)
            }
            return
        }
        let fm = FileManager.default
        let base = URL(fileURLWithPath: "/private/tmp").appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: base, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: base) }
        let root = base.appendingPathComponent("library")
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let audio = root.appendingPathComponent("fixture.wav")
        try Data([0, 1, 2]).write(to: audio)
        let collection = "00000000-0000-0000-0000-000000000001"
        let id = String(repeating: "a", count: 64)
        let link = "ocdj-reveal://track/\(collection)/\(id)"
        let request = try FinderReveal.Request(URL(string: link)!)
        var checks = 0
        func refused(_ work: () throws -> Void) {
            do { try work(); fatalError("Expected sanitized refusal") }
            catch { checks += 1 }
        }
        for bad in [link + "/", link + "?x=1", link + "#fragment", link.uppercased(),
                    link.replacingOccurrences(of: "track/", with: "track:80/"),
                    link.replacingOccurrences(of: "track/", with: "user@track/"),
                    link.replacingOccurrences(of: "track/", with: "other/"),
                    link.replacingOccurrences(of: collection, with: "not-a-uuid"),
                    link.replacingOccurrences(of: id, with: "../fixture.wav"),
                    link.replacingOccurrences(of: id, with: String(repeating: "a", count: 63)),
                    link.replacingOccurrences(of: id, with: "%61" + String(id.dropFirst())),
                    "file:///private/fixture.wav", "https://track/\(collection)/\(id)"] {
            refused { _ = try FinderReveal.Request(URL(string: bad)!) }
        }
        let mapFile = base.appendingPathComponent("map.json")
        func writeMap(relative: String = "fixture.wav", collectionID: String? = nil, rootPath: String? = nil) throws {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1,
                "collection_id": collectionID ?? collection, "root": rootPath ?? root.path,
                "tracks": [id: relative]])
            try data.write(to: mapFile)
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: mapFile.path)
        }
        try writeMap()
        let sandbox = Process()
        sandbox.executableURL = URL(fileURLWithPath: "/usr/bin/sandbox-exec")
        sandbox.arguments = ["-p", "(version 1)(allow default)(deny file-read* (literal \"\(base.path)\"))",
                             CommandLine.arguments[0], "--scoped", base.path]
        try sandbox.run()
        sandbox.waitUntilExit()
        guard sandbox.terminationStatus == 0 else { fatalError("Scoped sandbox regression failed") }
        checks += 1
        let map = try FinderReveal.load(request, from: mapFile)
        let selected = try FinderReveal.target(request, in: map)
        assert(selected == audio)
        checks += 1
        let alias = base.appendingPathComponent("alias")
        try fm.createSymbolicLink(at: alias, withDestinationURL: base)
        refused { _ = try FinderReveal.load(request, from: alias.appendingPathComponent("map.json")) }
        for unsafeRoot in [alias.appendingPathComponent("library").path, root.appendingPathComponent("nested").path] {
            if unsafeRoot.hasSuffix("/nested") {
                try fm.createSymbolicLink(atPath: unsafeRoot, withDestinationPath: root.path)
            }
            try writeMap(rootPath: unsafeRoot)
            refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
            if unsafeRoot.hasSuffix("/nested") { try fm.removeItem(atPath: unsafeRoot) }
        }
        for path in ["../fixture.wav", "/fixture.wav", "./fixture.wav", "nested/../fixture.wav",
                     "nested//fixture.wav", "", "ID3/fixture.wav", "TraxDB/fixture.wav", "missing.wav", "bad\0name"] {
            try writeMap(relative: path)
            refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        }
        let linkFile = root.appendingPathComponent("link.wav")
        try fm.createSymbolicLink(at: linkFile, withDestinationURL: audio)
        try writeMap(relative: "link.wav")
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        let nestedLink = root.appendingPathComponent("nested")
        try fm.createSymbolicLink(at: nestedLink, withDestinationURL: root)
        try writeMap(relative: "nested/fixture.wav")
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        try writeMap(relative: "nested") // directory/symlink refused
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        let directory = root.appendingPathComponent("directory")
        try fm.createDirectory(at: directory, withIntermediateDirectories: false)
        try writeMap(relative: "directory")
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        try writeMap(collectionID: "00000000-0000-0000-0000-000000000002")
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try writeMap(rootPath: "/")
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try writeMap()
        try fm.removeItem(at: audio)
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        try fm.setAttributes([.posixPermissions: 0o644], ofItemAtPath: mapFile.path)
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.removeItem(at: mapFile)
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.createSymbolicLink(at: mapFile, withDestinationURL: linkFile)
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.removeItem(at: mapFile)
        for invalid in ["{}", "{", "[]"] {
            try Data(invalid.utf8).write(to: mapFile)
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: mapFile.path)
            refused { _ = try FinderReveal.load(request, from: mapFile) }
        }
        try writeMap()
        let unknown = try FinderReveal.Request(URL(string: link.replacingOccurrences(of: id, with: String(repeating: "b", count: 64)))!)
        refused { _ = try FinderReveal.load(unknown, from: mapFile) }
        for invalidRoot in ["relative", "/private/../tmp", root.path + "/", root.path + "/./nested"] {
            try writeMap(rootPath: invalidRoot)
            refused { _ = try FinderReveal.load(request, from: mapFile) }
        }
        try writeMap()
        let hardlink = base.appendingPathComponent("hardlink.json")
        try fm.linkItem(at: mapFile, to: hardlink)
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.removeItem(at: hardlink)
        let oversized = try FileHandle(forWritingTo: mapFile)
        try oversized.truncate(atOffset: 32 * 1024 * 1024 + 1)
        try oversized.close()
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.removeItem(at: mapFile)
        guard mkfifo(mapFile.path, 0o600) == 0 else { fatalError("Fixture FIFO creation failed") }
        refused { _ = try FinderReveal.load(request, from: mapFile) }
        try fm.removeItem(at: mapFile)
        let fifo = root.appendingPathComponent("fifo.wav")
        guard mkfifo(fifo.path, 0o600) == 0 else { fatalError("Fixture FIFO creation failed") }
        try writeMap(relative: "fifo.wav")
        refused { _ = try FinderReveal.target(request, in: FinderReveal.load(request, from: mapFile)) }
        print("FinderReveal: \(checks) synthetic checks passed; no Finder/UI launched")
    }
}
