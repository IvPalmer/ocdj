import SwiftUI
import AppKit

struct SettingsView: View {
    @EnvironmentObject private var appModel: AppModel
    @State private var tgApiID: String = ""
    @State private var tgApiHash: String = ""
    @State private var tgPhone: String = ""
    @State private var tgCode: String = ""
    @State private var tgPassword: String = ""
    @State private var showTelegramAdvanced: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("Repo root").font(.headline)
                            Spacer()
                            Button("Choose…") { chooseRepo() }
                        }
                        Text("Select your `dj-tools` folder so the app can run scripts and read config.")
                            .foregroundStyle(.secondary)
                            .font(.caption)

                        Text(appModel.repoRoot?.path ?? "(not set)")
                            .font(.system(.body, design: .monospaced))
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("Artifacts").font(.headline)
                            Spacer()
                            Button("Open Logs Folder") { appModel.openLogsFolder() }
                        }
                        Text("Logs/reports are written under `repo/logs/`.")
                            .foregroundStyle(.secondary)
                            .font(.caption)
                    }
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("Telegram").font(.headline)
                            Spacer()
                            Toggle("Advanced", isOn: $showTelegramAdvanced)
                                .toggleStyle(.switch)
                                .font(.caption)
                        }

                        Text("For normal chats/groups we use the Bot API. For automating messages to other bots (e.g. @deezload2bot) we use a Telegram user session via TDLib.")
                            .foregroundStyle(.secondary)
                            .font(.caption)

                        if showTelegramAdvanced {
                            Divider().opacity(0.5)

                            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                                GridRow {
                                    Text("api_id")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .frame(width: 84, alignment: .trailing)
                                    TextField("123456", text: $tgApiID)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(maxWidth: 420)
                                }
                                GridRow {
                                    Text("api_hash")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .frame(width: 84, alignment: .trailing)
                                    SecureField("abcdef…", text: $tgApiHash)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(maxWidth: 420)
                                }
                                GridRow {
                                    Text("phone")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .frame(width: 84, alignment: .trailing)
                                    TextField("+15551234567", text: $tgPhone)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(maxWidth: 420)
                                }
                            }

                            HStack(spacing: 10) {
                                Button("Save") { saveTelegramUserCreds() }
                                Button("Connect") {
                                    saveTelegramUserCreds()
                                    appModel.telegramUserConnectIfPossible()
                                }
                                .buttonStyle(.borderedProminent)
                                Button("Disconnect") { appModel.telegramUserDisconnect() }
                            }

                            if appModel.telegramUserNeedsCode {
                                HStack(spacing: 10) {
                                    TextField("Login code", text: $tgCode)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(maxWidth: 260)
                                    Button("Submit code") { appModel.telegramUserSubmitCode(tgCode) }
                                }
                            }

                            if appModel.telegramUserNeedsPassword {
                                HStack(spacing: 10) {
                                    SecureField("2FA password", text: $tgPassword)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(maxWidth: 260)
                                    Button("Submit password") { appModel.telegramUserSubmitPassword(tgPassword) }
                                }
                            }

                            if let s = appModel.telegramUserStatusText {
                                Text(s)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
            }
            .padding(20)
        }
        .onAppear { loadTelegramUserCreds() }
    }

    private func chooseRepo() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.title = "Select dj-tools repo root"
        panel.prompt = "Select"
        panel.directoryURL = appModel.repoRoot
        if panel.runModal() == .OK {
            appModel.setRepoRoot(panel.url)
        }
    }

    private func loadTelegramUserCreds() {
        // Prefer Keychain values; if empty, fall back to djtools_config.json values.
        tgApiID = KeychainStore.read(service: "dj-tools", account: "telegram_user_api_id")
            ?? appModel.config?.telegramAppID
            ?? ""
        tgApiHash = KeychainStore.read(service: "dj-tools", account: "telegram_user_api_hash")
            ?? appModel.config?.telegramAppHash
            ?? ""
        tgPhone = KeychainStore.read(service: "dj-tools", account: "telegram_user_phone")
            ?? appModel.config?.telegramPhone
            ?? ""
    }

    private func saveTelegramUserCreds() {
        KeychainStore.write(service: "dj-tools", account: "telegram_user_api_id", value: tgApiID)
        KeychainStore.write(service: "dj-tools", account: "telegram_user_api_hash", value: tgApiHash)
        KeychainStore.write(service: "dj-tools", account: "telegram_user_phone", value: tgPhone)
    }
}


