import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var appModel: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("dj-tools")
                .font(.largeTitle.weight(.semibold))

            if let repo = appModel.repoRoot {
                LabeledContent("Repo", value: repo.path)
                LabeledContent("Logs", value: repo.appendingPathComponent("logs").path)
            } else {
                Text("Repo root not set. Go to Settings and select the dj-tools folder.")
                    .foregroundStyle(.secondary)
            }

            Divider()

            Text("Quick actions")
                .font(.headline)

            HStack {
                Button("TraxDB: Generate report") { appModel.selection = .traxdb }
                Button("Soulseek: Run wanted.txt") { appModel.selection = .soulseek }
                Button("Recognize: Add URL") { appModel.selection = .recognize }
            }
            .buttonStyle(.bordered)

            Spacer()
        }
        .padding(16)
    }
}


