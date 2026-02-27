import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appModel: AppModel

    var body: some View {
        NavigationSplitView {
            List(selection: $appModel.selection) {
                Section("Workflows") {
                    Label("Dashboard", systemImage: "rectangle.grid.2x2").tag(AppSection.dashboard)
                    Label("TraxDB", systemImage: "doc.text.magnifyingglass").tag(AppSection.traxdb)
                    Label("Soulseek", systemImage: "arrow.down.circle").tag(AppSection.soulseek)
                    Label("Recognize", systemImage: "music.note.list").tag(AppSection.recognize)
                }
                Section("System") {
                    Label("Jobs", systemImage: "clock").tag(AppSection.jobs)
                    Label("Settings", systemImage: "gear").tag(AppSection.settings)
                }
            }
            .listStyle(.sidebar)
        } detail: {
            switch appModel.selection {
            case .dashboard:
                DashboardView()
            case .traxdb:
                TraxDBView()
            case .soulseek:
                SoulseekView()
            case .recognize:
                RecognizeView()
            case .jobs:
                JobsView()
            case .settings:
                SettingsView()
            }
        }
        .navigationTitle(appModel.selection.title)
        .onAppear {
            appModel.bootstrapIfNeeded()
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
            .environmentObject(AppModel())
    }
}


