import Foundation
import AppKit
import CryptoKit
import Security
import Darwin

enum AppSection: Hashable {
    case dashboard
    case traxdb
    case soulseek
    case recognize
    case jobs
    case settings

    var title: String {
        switch self {
        case .dashboard: return "Dashboard"
        case .traxdb: return "TraxDB"
        case .soulseek: return "Soulseek"
        case .recognize: return "Recognize"
        case .jobs: return "Jobs"
        case .settings: return "Settings"
        }
    }
}

struct DJToolsConfig: Decodable {
    var djtoolsID3Root: String?
    var pixeldrainAPIKey: String?
    var traxdbCookies: String?
    var traxdbStartURL: String?
    var traxdbRequiresCookies: Bool?
    var ytDlpPath: String?
    var ffmpegPath: String?
    var recognizeConcurrentFragments: Int?
    var telegramBotToken: String?
    var telegramChatID: String?
    // Telegram MTProto (user session) for talking to other bots (TDLib)
    var telegramAppID: String?
    var telegramAppHash: String?
    var telegramPhone: String?
    var spotifyClientID: String?
    var spotifyClientSecret: String?
    var spotifyRedirectURI: String?
    var spotifyUserID: String?
    var spotifyPlaylistPublicDefault: Bool?
    var slskdBaseURL: String?
    var slskdAPIKey: String?
    var soulseekRoot: String?

    enum CodingKeys: String, CodingKey {
        case djtoolsID3Root = "djtools_id3_root"
        case pixeldrainAPIKey = "pixeldrain_api_key"
        case traxdbCookies = "traxdb_cookies"
        case traxdbStartURL = "traxdb_start_url"
        case traxdbRequiresCookies = "traxdb_requires_cookies"
        case ytDlpPath = "yt_dlp_path"
        case ffmpegPath = "ffmpeg_path"
        case recognizeConcurrentFragments = "recognize_concurrent_fragments"
        case telegramBotToken = "telegram_bot_token"
        case telegramChatID = "telegram_chat_id"
        case telegramAppID = "telegram_app_id"
        case telegramAppHash = "telegram_app_hash"
        case telegramPhone = "telegram_phone"
        case spotifyClientID = "spotify_client_id"
        case spotifyClientSecret = "spotify_client_secret"
        case spotifyRedirectURI = "spotify_redirect_uri"
        case spotifyUserID = "spotify_user_id"
        case spotifyPlaylistPublicDefault = "spotify_playlist_public_default"
        case slskdBaseURL = "slskd_base_url"
        case slskdAPIKey = "slskd_api_key"
        case soulseekRoot = "soulseek_root"
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var selection: AppSection = .dashboard
    @Published var jobsStore = JobsStore()
    @Published var repoRoot: URL? = nil
    @Published var config: DJToolsConfig? = nil
    @Published var configError: String? = nil
    let recognizeModel = RecognizeModel()
    @Published var spotifyStatusText: String? = nil
    @Published var spotifyPlaylistURL: URL? = nil
    @Published var soulseekWantedStatusText: String? = nil

    // Telegram user-session sender (TDLib / MTProto)
    @Published var telegramUserStatusText: String? = nil
    @Published var telegramUserIsReady: Bool = false
    @Published var telegramUserNeedsCode: Bool = false
    @Published var telegramUserNeedsPassword: Bool = false

    private let telegramUser = TelegramUserClient()

    private let repoRootDefaultsKey = "djtools.repoRoot"
    private let spotifyTokenDefaultsKey = "djtools.spotify.token"
    private let spotifyPKCEDefaultsKey = "djtools.spotify.pkce"
    private let spotifyPendingPlaylistDefaultsKey = "djtools.spotify.pendingPlaylist"
    private var spotifyAuthContinuation: CheckedContinuation<Void, Error>? = nil
    private var spotifyPKCEVerifier: String? = nil
    private var spotifyAuthState: String? = nil

    init() {
        if let s = UserDefaults.standard.string(forKey: repoRootDefaultsKey) {
            repoRoot = URL(fileURLWithPath: s)
        } else {
            repoRoot = RepoLocator.findRepoRoot()
        }
        reloadConfig()
        jobsStore.setRepoRoot(repoRoot)

        Task { [weak self] in
            guard let self else { return }
            await telegramUser.setOnStateUpdate { [weak self] state in
                guard let self else { return }
                Task { @MainActor in
                    self.telegramUserStatusText = state.status
                    self.telegramUserIsReady = state.isReady
                    self.telegramUserNeedsCode = state.needsCode
                    self.telegramUserNeedsPassword = state.needsPassword
                }
            }
        }
    }

    func setRepoRoot(_ url: URL?) {
        repoRoot = url
        if let url {
            UserDefaults.standard.set(url.path, forKey: repoRootDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: repoRootDefaultsKey)
        }
        reloadConfig()
        jobsStore.setRepoRoot(repoRoot)
    }

    func reloadConfig() {
        config = nil
        configError = nil
        guard let repoRoot else { return }
        let path = repoRoot.appendingPathComponent("djtools_config.json")
        do {
            let data = try Data(contentsOf: path)
            let decoder = JSONDecoder()
            config = try decoder.decode(DJToolsConfig.self, from: data)
        } catch {
            configError = "Failed to load djtools_config.json: \(error)"
        }
    }

    func bootstrapIfNeeded() {
        // Ensure logs folder exists (repo-local artifacts).
        guard let repoRoot else { return }
        let logs = repoRoot.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
    }

    func openLogsFolder() {
        guard let repoRoot else { return }
        let logs = repoRoot.appendingPathComponent("logs", isDirectory: true)
        NSWorkspace.shared.open(logs)
    }

    func openConfigFile() {
        guard let repoRoot else { return }
        let url = repoRoot.appendingPathComponent("djtools_config.json", isDirectory: false)
        NSWorkspace.shared.open(url)
    }

    func updateConfigValue(key: String, value: Any) {
        guard let repoRoot else { return }
        let path = repoRoot.appendingPathComponent("djtools_config.json", isDirectory: false)
        do {
            let data = try Data(contentsOf: path)
            let obj = try JSONSerialization.jsonObject(with: data, options: [])
            var dict = (obj as? [String: Any]) ?? [:]
            dict[key] = value
            let out = try JSONSerialization.data(withJSONObject: dict, options: [.prettyPrinted, .sortedKeys])
            try out.write(to: path, options: [.atomic])
            reloadConfig()
        } catch {
            configError = "Failed to update djtools_config.json: \(error)"
        }
    }

    func handleIncomingURL(_ url: URL) {
        // Expected: djtools://oauth/spotify?code=...&state=...
        guard url.scheme == "djtools", url.host == "oauth", url.path == "/spotify" else { return }
        Task { @MainActor in
            do {
                try await spotifyHandleCallback(url: url)
                spotifyStatusText = "Spotify connected."
                spotifyAuthContinuation?.resume()
                spotifyAuthContinuation = nil
                await spotifyPerformPendingPlaylistIfAny()
            } catch {
                spotifyStatusText = "Spotify auth failed: \(error)"
                spotifyAuthContinuation?.resume(throwing: error)
                spotifyAuthContinuation = nil
            }
        }
    }

    // MARK: - Telegram User (TDLib)

    func telegramUserConnectIfPossible() {
        // Prefer Keychain (safer), but fall back to djtools_config.json keys if present.
        let apiID = (KeychainStore.read(service: "dj-tools", account: "telegram_user_api_id") ?? config?.telegramAppID ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let apiHash = (KeychainStore.read(service: "dj-tools", account: "telegram_user_api_hash") ?? config?.telegramAppHash ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let phone = (KeychainStore.read(service: "dj-tools", account: "telegram_user_phone") ?? config?.telegramPhone ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !apiID.isEmpty, !apiHash.isEmpty, !phone.isEmpty else {
            telegramUserStatusText = "Telegram User not configured. Set api_id/api_hash/phone in Settings (or add telegram_app_id/telegram_app_hash/telegram_phone to djtools_config.json)."
            return
        }
        Task { await telegramUser.connect(apiID: apiID, apiHash: apiHash, phoneNumber: phone) }
    }

    func telegramUserDisconnect() {
        Task { await telegramUser.disconnect() }
    }

    func telegramUserSubmitCode(_ code: String) {
        Task { await telegramUser.submitCode(code) }
    }

    func telegramUserSubmitPassword(_ password: String) {
        Task { await telegramUser.submitPassword(password) }
    }

    func telegramUserSendToBotUsername(_ username: String, text: String) async throws {
        try await telegramUser.sendMessage(toUsername: username, text: text)
    }
}

// MARK: - Keychain

enum KeychainStore {
    static func read(service: String, account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        guard status == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(service: String, account: String, value: String) {
        let data = value.data(using: .utf8) ?? Data()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attrs: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add[kSecValueData as String] = data
            _ = SecItemAdd(add as CFDictionary, nil)
        }
    }
}

// MARK: - Telegram User (TDLib / tdjson)

private struct TelegramUserState {
    var status: String
    var isReady: Bool
    var needsCode: Bool
    var needsPassword: Bool
}

private actor TelegramUserClient {
    // Callback on main actor is set by AppModel.
    private var onStateUpdate: (@Sendable (TelegramUserState) -> Void)?

    private var lib: TDJsonLib?
    private var client: UnsafeMutableRawPointer?
    private var runTask: Task<Void, Never>?

    private var apiID: String = ""
    private var apiHash: String = ""
    private var phoneNumber: String = ""

    private var wantsCode = false
    private var wantsPassword = false
    private var ready = false

    // Simple response waiters keyed by @extra
    private var waiters: [String: CheckedContinuation<[String: Any], Error>] = [:]

    func setOnStateUpdate(_ cb: (@Sendable (TelegramUserState) -> Void)?) {
        onStateUpdate = cb
    }

    func connect(apiID: String, apiHash: String, phoneNumber: String) async {
        self.apiID = apiID
        self.apiHash = apiHash
        self.phoneNumber = phoneNumber

        await disconnect()

        guard let lib = TDJsonLib.load() else {
            publish(status: "TDLib not found. Install via `brew install tdlib` and restart dj-tools.", ready: false, code: false, password: false)
            return
        }
        self.lib = lib
        self.client = lib.create()

        guard self.client != nil else {
            publish(status: "Failed to create TDLib client.", ready: false, code: false, password: false)
            self.lib = nil
            self.client = nil
            return
        }

        publish(status: "Connecting Telegram user session…", ready: false, code: false, password: false)
        runTask = Task.detached(priority: .utility) { [weak self] in
            guard let self else { return }
            await self.loop()
        }
    }

    func disconnect() async {
        runTask?.cancel()
        if let runTask {
            _ = await runTask.value
        }
        runTask = nil

        // Fail any outstanding requests so callers don't hang forever.
        let toFail = Array(waiters.values)
        waiters.removeAll()
        for cont in toFail {
            cont.resume(throwing: NSError(
                domain: "djtools.telegram.user",
                code: 999,
                userInfo: [NSLocalizedDescriptionKey: "Telegram user session disconnected."]
            ))
        }

        wantsCode = false
        wantsPassword = false
        ready = false

        if let lib, let client {
            lib.destroy(client)
        }
        client = nil
        lib = nil

        publish(status: "Telegram user session disconnected.", ready: false, code: false, password: false)
    }

    func submitCode(_ code: String) {
        let c = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !c.isEmpty else { return }
        send(["@type": "checkAuthenticationCode", "code": c])
    }

    func submitPassword(_ password: String) {
        let p = password.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !p.isEmpty else { return }
        send(["@type": "checkAuthenticationPassword", "password": p])
    }

    func sendMessage(toUsername username: String, text: String) async throws {
        guard ready else {
            throw NSError(domain: "djtools.telegram.user", code: 1, userInfo: [NSLocalizedDescriptionKey: "Telegram user session not connected (not ready)."])
        }
        let u = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !u.isEmpty else {
            throw NSError(domain: "djtools.telegram.user", code: 2, userInfo: [NSLocalizedDescriptionKey: "Username is empty."])
        }
        let norm = u.hasPrefix("@") ? String(u.dropFirst()) : u
        let chat = try await request(["@type": "searchPublicChat", "username": norm])
        guard let chatID = chat["id"] as? Int64 else {
            throw NSError(domain: "djtools.telegram.user", code: 3, userInfo: [NSLocalizedDescriptionKey: "Failed to resolve @\(norm) to a chat."])
        }
        _ = try await request([
            "@type": "sendMessage",
            "chat_id": chatID,
            "input_message_content": [
                "@type": "inputMessageText",
                "text": [
                    "@type": "formattedText",
                    "text": text,
                ],
                "disable_web_page_preview": true,
                "clear_draft": false,
            ],
        ])
    }

    // MARK: - TDLib loop / dispatch

    private func publish(status: String, ready: Bool, code: Bool, password: Bool) {
        onStateUpdate?(TelegramUserState(status: status, isReady: ready, needsCode: code, needsPassword: password))
    }

    private func loop() async {
        // Use non-blocking receive (timeout 0) so this actor remains responsive to submits/requests.
        while !Task.isCancelled {
            guard let lib, let client else { break }
            if let cstr = lib.receive(client, timeout: 0.0) {
                let s = String(cString: cstr)
                handle(json: s)
            } else {
                // Yield to other actor messages.
                try? await Task.sleep(nanoseconds: 50_000_000) // 50ms
            }
        }
    }

    private func handle(json: String) {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] else { return }

        // Complete waiter by @extra
        if let extra = obj["@extra"] as? String, let cont = waiters.removeValue(forKey: extra) {
            if let type = obj["@type"] as? String, type == "error" {
                let msg = (obj["message"] as? String) ?? "Unknown error"
                cont.resume(throwing: NSError(domain: "djtools.telegram.user", code: 100, userInfo: [NSLocalizedDescriptionKey: msg]))
            } else {
                cont.resume(returning: obj)
            }
            return
        }

        guard let type = obj["@type"] as? String else { return }
        if type == "updateAuthorizationState" {
            if let state = obj["authorization_state"] as? [String: Any],
               let st = state["@type"] as? String {
                handleAuth(stateType: st, state: state)
            }
        }
    }

    private func handleAuth(stateType: String, state: [String: Any]) {
        switch stateType {
        case "authorizationStateWaitTdlibParameters":
            publish(status: "Telegram: initializing…", ready: false, code: false, password: false)
            let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            let dbDir = (appSupport?.appendingPathComponent("dj-tools/tdlib", isDirectory: true).path) ?? (NSTemporaryDirectory() + "/dj-tools-tdlib")
            try? FileManager.default.createDirectory(atPath: dbDir, withIntermediateDirectories: true)
            send([
                "@type": "setTdlibParameters",
                "parameters": [
                    "@type": "tdlibParameters",
                    "use_test_dc": false,
                    "database_directory": dbDir,
                    "files_directory": dbDir,
                    "use_file_database": true,
                    "use_chat_info_database": true,
                    "use_message_database": true,
                    "use_secret_chats": false,
                    "api_id": Int(apiID) ?? 0,
                    "api_hash": apiHash,
                    "system_language_code": "en",
                    "device_model": "macOS",
                    "system_version": ProcessInfo.processInfo.operatingSystemVersionString,
                    "application_version": "dj-tools",
                    "enable_storage_optimizer": true,
                ],
            ])
        case "authorizationStateWaitEncryptionKey":
            publish(status: "Telegram: unlocking database…", ready: false, code: false, password: false)
            send(["@type": "checkDatabaseEncryptionKey", "encryption_key": ""])
        case "authorizationStateWaitPhoneNumber":
            wantsCode = false
            wantsPassword = false
            ready = false
            publish(status: "Telegram: waiting for phone number…", ready: false, code: false, password: false)
            send(["@type": "setAuthenticationPhoneNumber", "phone_number": phoneNumber])
        case "authorizationStateWaitCode":
            wantsCode = true
            wantsPassword = false
            ready = false
            publish(status: "Telegram: enter the login code (sent to Telegram/SMS).", ready: false, code: true, password: false)
        case "authorizationStateWaitPassword":
            wantsCode = false
            wantsPassword = true
            ready = false
            publish(status: "Telegram: enter your 2FA password.", ready: false, code: false, password: true)
        case "authorizationStateReady":
            wantsCode = false
            wantsPassword = false
            ready = true
            publish(status: "Telegram user session ready.", ready: true, code: false, password: false)
        case "authorizationStateLoggingOut":
            publish(status: "Telegram: logging out…", ready: false, code: false, password: false)
        case "authorizationStateClosing":
            publish(status: "Telegram: closing…", ready: false, code: false, password: false)
        case "authorizationStateClosed":
            publish(status: "Telegram: closed.", ready: false, code: false, password: false)
        default:
            publish(status: "Telegram: \(stateType)", ready: false, code: wantsCode, password: wantsPassword)
        }
    }

    private func send(_ obj: [String: Any]) {
        guard let lib, let client else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
              let s = String(data: data, encoding: .utf8) else { return }
        lib.send(client, s)
    }

    private func request(_ obj: [String: Any]) async throws -> [String: Any] {
        let extra = UUID().uuidString
        var payload = obj
        payload["@extra"] = extra
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<[String: Any], Error>) in
            waiters[extra] = cont
            send(payload)
        }
    }
}

private struct TDJsonLib {
    typealias CreateFn = @convention(c) () -> UnsafeMutableRawPointer?
    typealias SendFn = @convention(c) (UnsafeMutableRawPointer?, UnsafePointer<CChar>?) -> Void
    typealias ReceiveFn = @convention(c) (UnsafeMutableRawPointer?, Double) -> UnsafePointer<CChar>?
    typealias DestroyFn = @convention(c) (UnsafeMutableRawPointer?) -> Void

    let handle: UnsafeMutableRawPointer
    let createFn: CreateFn
    let sendFn: SendFn
    let receiveFn: ReceiveFn
    let destroyFn: DestroyFn

    static func load() -> TDJsonLib? {
        // Common Homebrew locations.
        let candidates = [
            "/opt/homebrew/lib/libtdjson.dylib",
            "/usr/local/lib/libtdjson.dylib",
            "libtdjson.dylib",
        ]
        for path in candidates {
            if let h = dlopen(path, RTLD_NOW) {
                guard
                    let c = dlsym(h, "td_json_client_create"),
                    let s = dlsym(h, "td_json_client_send"),
                    let r = dlsym(h, "td_json_client_receive"),
                    let d = dlsym(h, "td_json_client_destroy")
                else {
                    dlclose(h)
                    continue
                }
                return TDJsonLib(
                    handle: h,
                    createFn: unsafeBitCast(c, to: CreateFn.self),
                    sendFn: unsafeBitCast(s, to: SendFn.self),
                    receiveFn: unsafeBitCast(r, to: ReceiveFn.self),
                    destroyFn: unsafeBitCast(d, to: DestroyFn.self)
                )
            }
        }
        return nil
    }

    func create() -> UnsafeMutableRawPointer? { createFn() }
    func send(_ client: UnsafeMutableRawPointer, _ json: String) {
        json.withCString { cstr in
            sendFn(client, cstr)
        }
    }
    func receive(_ client: UnsafeMutableRawPointer, timeout: Double) -> UnsafePointer<CChar>? {
        receiveFn(client, timeout)
    }
    func destroy(_ client: UnsafeMutableRawPointer) {
        destroyFn(client)
    }
}

// MARK: - Spotify

@MainActor
extension AppModel {
    struct SpotifyPKCEState: Codable {
        var verifier: String
        var state: String
    }

    struct SpotifyPendingPlaylist: Codable {
        struct Query: Codable {
            var title: String
            var artist: String?
            var isrc: String?
        }
        var name: String
        var queries: [Query]
    }

    struct SpotifyToken: Codable {
        var accessToken: String
        var refreshToken: String?
        var expiresAt: Date
        var tokenType: String
        var scope: String?
        var isExpired: Bool { Date() >= expiresAt.addingTimeInterval(-60) }
    }

    private func spotifyLoadToken() -> SpotifyToken? {
        // Migrate from UserDefaults to Keychain if needed
        if let legacyData = UserDefaults.standard.data(forKey: spotifyTokenDefaultsKey) {
            if let token = try? JSONDecoder().decode(SpotifyToken.self, from: legacyData) {
                spotifySaveToken(token)
                UserDefaults.standard.removeObject(forKey: spotifyTokenDefaultsKey)
                return token
            }
        }
        guard let json = KeychainStore.read(service: "dj-tools", account: "spotify_token"),
              let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(SpotifyToken.self, from: data)
    }

    private func spotifySaveToken(_ token: SpotifyToken) {
        if let data = try? JSONEncoder().encode(token),
           let json = String(data: data, encoding: .utf8) {
            KeychainStore.write(service: "dj-tools", account: "spotify_token", value: json)
        }
    }

    private func spotifyLoadPKCE() -> SpotifyPKCEState? {
        guard let data = UserDefaults.standard.data(forKey: spotifyPKCEDefaultsKey) else { return nil }
        return try? JSONDecoder().decode(SpotifyPKCEState.self, from: data)
    }

    private func spotifySavePKCE(_ pkce: SpotifyPKCEState) {
        if let data = try? JSONEncoder().encode(pkce) {
            UserDefaults.standard.set(data, forKey: spotifyPKCEDefaultsKey)
        }
    }

    private func spotifyClearPKCE() {
        UserDefaults.standard.removeObject(forKey: spotifyPKCEDefaultsKey)
    }

    private func spotifyLoadPendingPlaylist() -> SpotifyPendingPlaylist? {
        guard let data = UserDefaults.standard.data(forKey: spotifyPendingPlaylistDefaultsKey) else { return nil }
        return try? JSONDecoder().decode(SpotifyPendingPlaylist.self, from: data)
    }

    private func spotifySavePendingPlaylist(_ pending: SpotifyPendingPlaylist) {
        if let data = try? JSONEncoder().encode(pending) {
            UserDefaults.standard.set(data, forKey: spotifyPendingPlaylistDefaultsKey)
        }
    }

    private func spotifyClearPendingPlaylist() {
        UserDefaults.standard.removeObject(forKey: spotifyPendingPlaylistDefaultsKey)
    }

    private func spotifyBase64URLEncode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private func spotifyRandomString(_ n: Int) -> String {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        return String((0..<n).compactMap { _ in alphabet.randomElement() })
    }

    private func spotifyStartAuth() async throws {
        guard let cfg = config else { throw NSError(domain: "djtools.spotify", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing config"]) }
        guard let clientID = cfg.spotifyClientID, !clientID.isEmpty else {
            throw NSError(domain: "djtools.spotify", code: 2, userInfo: [NSLocalizedDescriptionKey: "Missing spotify_client_id in djtools_config.json"])
        }
        let redirectURI = (cfg.spotifyRedirectURI?.isEmpty == false) ? cfg.spotifyRedirectURI! : "djtools://oauth/spotify"

        let verifier = spotifyRandomString(64)
        let challenge = spotifyBase64URLEncode(Data(SHA256.hash(data: Data(verifier.utf8))))
        let state = spotifyRandomString(32)

        spotifyPKCEVerifier = verifier
        spotifyAuthState = state
        spotifySavePKCE(SpotifyPKCEState(verifier: verifier, state: state))

        // Scopes for playlist creation
        let scope = [
            "playlist-modify-private",
            "playlist-modify-public",
            "playlist-read-private",
            "user-read-email",
            "user-read-private",
        ].joined(separator: " ")

        var comps = URLComponents(string: "https://accounts.spotify.com/authorize")!
        comps.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "scope", value: scope),
        ]

        guard let url = comps.url else { throw NSError(domain: "djtools.spotify", code: 3, userInfo: [NSLocalizedDescriptionKey: "Failed to build auth URL"]) }
        NSWorkspace.shared.open(url)
        spotifyStatusText = "Complete Spotify login in your browser… (playlist will be created automatically)"
    }

    private func spotifyHandleCallback(url: URL) async throws {
        guard let cfg = config else { throw NSError(domain: "djtools.spotify", code: 10, userInfo: [NSLocalizedDescriptionKey: "Missing config"]) }
        guard let clientID = cfg.spotifyClientID, !clientID.isEmpty else { throw NSError(domain: "djtools.spotify", code: 11, userInfo: [NSLocalizedDescriptionKey: "Missing spotify_client_id"]) }
        let redirectURI = (cfg.spotifyRedirectURI?.isEmpty == false) ? cfg.spotifyRedirectURI! : "djtools://oauth/spotify"

        guard let comps = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let items = comps.queryItems else {
            throw NSError(domain: "djtools.spotify", code: 12, userInfo: [NSLocalizedDescriptionKey: "Invalid callback URL"])
        }
        if let err = items.first(where: { $0.name == "error" })?.value {
            throw NSError(domain: "djtools.spotify", code: 13, userInfo: [NSLocalizedDescriptionKey: "Spotify error: \(err)"])
        }
        let code = items.first(where: { $0.name == "code" })?.value
        let state = items.first(where: { $0.name == "state" })?.value
        guard let code, !code.isEmpty else { throw NSError(domain: "djtools.spotify", code: 14, userInfo: [NSLocalizedDescriptionKey: "Missing code"]) }
        let pkce = spotifyLoadPKCE()
        let expectedState = spotifyAuthState ?? pkce?.state
        let verifier = spotifyPKCEVerifier ?? pkce?.verifier
        guard state == expectedState else { throw NSError(domain: "djtools.spotify", code: 15, userInfo: [NSLocalizedDescriptionKey: "State mismatch"]) }
        guard let verifier, !verifier.isEmpty else { throw NSError(domain: "djtools.spotify", code: 16, userInfo: [NSLocalizedDescriptionKey: "Missing PKCE verifier"]) }

        var req = URLRequest(url: URL(string: "https://accounts.spotify.com/api/token")!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var bodyComps = URLComponents()
        bodyComps.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "grant_type", value: "authorization_code"),
            URLQueryItem(name: "code", value: code),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_verifier", value: verifier),
        ]
        req.httpBody = bodyComps.query?.data(using: .utf8)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw NSError(domain: "djtools.spotify", code: 17, userInfo: [NSLocalizedDescriptionKey: "Invalid response"]) }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(non-utf8 body)"
            throw NSError(domain: "djtools.spotify", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "Token exchange failed: HTTP \(http.statusCode): \(body)"])
        }

        struct TokenResponse: Decodable {
            var access_token: String
            var token_type: String
            var scope: String?
            var expires_in: Int
            var refresh_token: String?
        }
        let tr = try JSONDecoder().decode(TokenResponse.self, from: data)
        spotifySaveToken(SpotifyToken(
            accessToken: tr.access_token,
            refreshToken: tr.refresh_token,
            expiresAt: Date().addingTimeInterval(TimeInterval(tr.expires_in)),
            tokenType: tr.token_type,
            scope: tr.scope
        ))
        spotifyClearPKCE()
    }

    private func spotifyRefreshIfNeeded() async throws -> SpotifyToken {
        guard let cfg = config else { throw NSError(domain: "djtools.spotify", code: 30, userInfo: [NSLocalizedDescriptionKey: "Missing config"]) }
        guard let clientID = cfg.spotifyClientID, !clientID.isEmpty else { throw NSError(domain: "djtools.spotify", code: 31, userInfo: [NSLocalizedDescriptionKey: "Missing spotify_client_id"]) }
        guard var token = spotifyLoadToken() else { throw NSError(domain: "djtools.spotify", code: 32, userInfo: [NSLocalizedDescriptionKey: "Not connected to Spotify"]) }
        if !token.isExpired { return token }
        guard let refresh = token.refreshToken, !refresh.isEmpty else {
            throw NSError(domain: "djtools.spotify", code: 34, userInfo: [NSLocalizedDescriptionKey: "Spotify session expired; reconnect required."])
        }

        var req = URLRequest(url: URL(string: "https://accounts.spotify.com/api/token")!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var bodyComps = URLComponents()
        bodyComps.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "grant_type", value: "refresh_token"),
            URLQueryItem(name: "refresh_token", value: refresh),
        ]
        req.httpBody = bodyComps.query?.data(using: .utf8)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw NSError(domain: "djtools.spotify", code: 33, userInfo: [NSLocalizedDescriptionKey: "Invalid response"]) }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(non-utf8 body)"
            throw NSError(domain: "djtools.spotify", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "Token refresh failed: HTTP \(http.statusCode): \(body)"])
        }

        struct RefreshResponse: Decodable {
            var access_token: String
            var token_type: String
            var scope: String?
            var expires_in: Int
            var refresh_token: String?
        }
        let rr = try JSONDecoder().decode(RefreshResponse.self, from: data)
        token.accessToken = rr.access_token
        token.tokenType = rr.token_type
        token.scope = rr.scope
        token.expiresAt = Date().addingTimeInterval(TimeInterval(rr.expires_in))
        if let newRefresh = rr.refresh_token, !newRefresh.isEmpty { token.refreshToken = newRefresh }
        spotifySaveToken(token)
        return token
    }

    func spotifyEnsureConnected() async throws {
        if let tok = spotifyLoadToken(), !tok.isExpired { return }
        if let tok = spotifyLoadToken(), (tok.refreshToken?.isEmpty == false) {
            _ = try await spotifyRefreshIfNeeded()
            return
        }
        try await spotifyStartAuth()
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            spotifyAuthContinuation = cont
        }
    }

    private func spotifyAPIRequest(_ method: String, _ path: String, token: SpotifyToken, query: [URLQueryItem] = [], jsonBody: Data? = nil) async throws -> (Data, HTTPURLResponse) {
        var comps = URLComponents(string: "https://api.spotify.com\(path)")!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.setValue("Bearer \(token.accessToken)", forHTTPHeaderField: "Authorization")
        if let jsonBody {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = jsonBody
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw NSError(domain: "djtools.spotify", code: 40, userInfo: [NSLocalizedDescriptionKey: "Invalid response"]) }
        return (data, http)
    }

    private func spotifyPerformPendingPlaylistIfAny() async {
        guard let pending = spotifyLoadPendingPlaylist() else { return }
        // Don't clear pending until we succeed; this makes the flow more resilient to transient failures.
        await spotifyCreatePlaylist(trackQueries: pending.queries.map { ($0.title, $0.artist, $0.isrc) }, name: pending.name)
    }

    func spotifyCreatePlaylist(trackQueries: [(title: String, artist: String?, isrc: String?)], name: String) async {
        spotifyStatusText = nil
        spotifyPlaylistURL = nil
        do {
            // If not connected yet, kick off auth and resume automatically after callback.
            if spotifyLoadToken() == nil {
                spotifySavePendingPlaylist(
                    SpotifyPendingPlaylist(
                        name: name,
                        queries: trackQueries.map { .init(title: $0.title, artist: $0.artist, isrc: $0.isrc) }
                    )
                )
                try await spotifyStartAuth()
                return
            }

            try await spotifyEnsureConnected()
            let token = try await spotifyRefreshIfNeeded()

            // Resolve user id
            let userID = (config?.spotifyUserID ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let resolvedUserID: String
            if !userID.isEmpty {
                resolvedUserID = userID
            } else {
                let (data, http) = try await spotifyAPIRequest("GET", "/v1/me", token: token)
                guard (200..<300).contains(http.statusCode) else { throw NSError(domain: "djtools.spotify", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "GET /me failed"]) }
                struct Me: Decodable { var id: String }
                resolvedUserID = try JSONDecoder().decode(Me.self, from: data).id
            }

            // Search each track -> Spotify URI
            var uris: [String] = []
            var seenURIs = Set<String>()
            for q in trackQueries {
                let query: String
                if let isrc = q.isrc, !isrc.isEmpty {
                    query = "isrc:\(isrc)"
                } else {
                    let artist = q.artist?.isEmpty == false ? " artist:\(q.artist!)" : ""
                    query = "track:\(q.title)\(artist)"
                }

                let (data, http) = try await spotifyAPIRequest(
                    "GET",
                    "/v1/search",
                    token: token,
                    query: [
                        URLQueryItem(name: "q", value: query),
                        URLQueryItem(name: "type", value: "track"),
                        URLQueryItem(name: "limit", value: "1"),
                    ]
                )
                guard (200..<300).contains(http.statusCode) else { continue }
                struct Search: Decodable {
                    struct Tracks: Decodable {
                        struct Item: Decodable { var uri: String }
                        var items: [Item]
                    }
                    var tracks: Tracks
                }
                if let uri = try? JSONDecoder().decode(Search.self, from: data).tracks.items.first?.uri {
                    if seenURIs.insert(uri).inserted {
                        uris.append(uri)
                    }
                }
            }

            if uris.isEmpty {
                spotifyStatusText = "No Spotify matches found for these tracks."
                return
            }

            let isPublic = config?.spotifyPlaylistPublicDefault ?? false
            let body = try JSONSerialization.data(withJSONObject: [
                "name": name,
                "description": "Created by dj-tools from ShazamKit matches",
                "public": isPublic,
            ])
            let (createData, createHTTP) = try await spotifyAPIRequest("POST", "/v1/users/\(resolvedUserID)/playlists", token: token, jsonBody: body)
            guard (200..<300).contains(createHTTP.statusCode) else {
                let s = String(data: createData, encoding: .utf8) ?? ""
                throw NSError(domain: "djtools.spotify", code: createHTTP.statusCode, userInfo: [NSLocalizedDescriptionKey: "Create playlist failed: \(s)"])
            }
            struct Playlist: Decodable { var id: String; var external_urls: [String: String]? }
            let playlist = try JSONDecoder().decode(Playlist.self, from: createData)

            // Add tracks in batches
            for chunk in stride(from: 0, to: uris.count, by: 100) {
                let part = Array(uris[chunk..<min(chunk + 100, uris.count)])
                let addBody = try JSONSerialization.data(withJSONObject: ["uris": part])
                let (addData, addHTTP) = try await spotifyAPIRequest("POST", "/v1/playlists/\(playlist.id)/tracks", token: token, jsonBody: addBody)
                guard (200..<300).contains(addHTTP.statusCode) else {
                    let s = String(data: addData, encoding: .utf8) ?? ""
                    throw NSError(domain: "djtools.spotify", code: addHTTP.statusCode, userInfo: [NSLocalizedDescriptionKey: "Add tracks failed: \(s)"])
                }
            }

            if let urlStr = playlist.external_urls?["spotify"], let url = URL(string: urlStr) {
                spotifyPlaylistURL = url
                NSWorkspace.shared.open(url)
            }
            spotifyStatusText = "Created Spotify playlist with \(uris.count) tracks."
            spotifyClearPendingPlaylist()
        } catch {
            spotifyStatusText = "Spotify playlist failed: \(error)"
        }
    }
}

// MARK: - Soulseek wanted.txt

@MainActor
extension AppModel {
    func soulseekAppendWanted(lines: [String]) async {
        soulseekWantedStatusText = nil
        guard let repo = repoRoot else {
            soulseekWantedStatusText = "Repo root not set."
            return
        }
        let wantedURL = repo.appendingPathComponent("tools/soulseek_sync/wanted.txt")

        let cleaned: [String] = lines
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        if cleaned.isEmpty {
            soulseekWantedStatusText = "No tracks to add."
            return
        }

        let logsDir = repo.appendingPathComponent("logs", isDirectory: true)
        let ts = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
        let logPath = logsDir.appendingPathComponent("soulseek_wanted_\(ts).log").path

        var job = Job(
            title: "Soulseek: append wanted.txt",
            commandLine: ["append", wantedURL.path] + cleaned,
            workingDirectory: repo.path,
            status: .running
        )
        job.startedAt = Date()
        job.logPath = logPath
        job.artifactsPath = wantedURL.deletingLastPathComponent().path
        job.finalPath = wantedURL.path
        jobsStore.add(job)

        do {
            try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: logPath, contents: nil)
            let logHandle = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
            defer { try? logHandle.close() }

            func writeLog(_ s: String) {
                try? logHandle.write(contentsOf: Data(s.utf8))
            }

            // Ensure wanted.txt exists.
            if !FileManager.default.fileExists(atPath: wantedURL.path) {
                try FileManager.default.createDirectory(at: wantedURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                FileManager.default.createFile(atPath: wantedURL.path, contents: nil)
            }

            let existingData = (try? Data(contentsOf: wantedURL)) ?? Data()
            let existingText = String(data: existingData, encoding: .utf8) ?? ""
            let existingSet = Set(
                existingText
                    .split(separator: "\n", omittingEmptySubsequences: false)
                    .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty && !$0.hasPrefix("#") }
            )

            var toAppend: [String] = []
            for line in cleaned {
                if !existingSet.contains(line) {
                    toAppend.append(line)
                }
            }

            if toAppend.isEmpty {
                soulseekWantedStatusText = "All \(cleaned.count) already in wanted.txt."
                writeLog("No new lines to append.\n")
            } else {
                let prefix = existingText.hasSuffix("\n") || existingText.isEmpty ? "" : "\n"
                let block = prefix + toAppend.joined(separator: "\n") + "\n"
                if let handle = try? FileHandle(forWritingTo: wantedURL) {
                    defer { try? handle.close() }
                    _ = try? handle.seekToEnd()
                    try? handle.write(contentsOf: Data(block.utf8))
                } else {
                    // Fallback
                    var s = existingText
                    s.append(block)
                    try s.data(using: .utf8)?.write(to: wantedURL, options: [.atomic])
                }
                soulseekWantedStatusText = "Added \(toAppend.count) to wanted.txt."
                writeLog("Appended \(toAppend.count) lines to \(wantedURL.path)\n")
            }

            job.status = .succeeded
            job.exitCode = 0
            job.endedAt = Date()
            jobsStore.update(job)
        } catch {
            soulseekWantedStatusText = "Failed to write wanted.txt: \(error)"
            job.status = .failed
            job.exitCode = 1
            job.endedAt = Date()
            job.errorMessage = String(describing: error)
            jobsStore.update(job)
        }
    }
}


