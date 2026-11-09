import Foundation
import Darwin

@main
struct FinderRevealTests {
    static func main() throws {
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
        let map = try FinderReveal.load(request, from: mapFile)
        let selected = try FinderReveal.target(request, in: map)
        assert(selected == audio)
        checks += 1
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
