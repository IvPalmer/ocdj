import SwiftUI

struct ReadmeView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Notes").font(.title3.weight(.semibold))
            Text("This is a minimal local UI that runs the existing scripts under `tools/` and stores artifacts under `repo/logs/`.")
                .foregroundStyle(.secondary)
            Text("If a command fails to run, make sure you opened the correct repo root in Settings.")
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(16)
    }
}


