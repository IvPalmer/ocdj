//
//  AppDelegate.swift
//  OCDJ Helper
//
//  Created by Raphael Palmer on 17/04/26.
//

import Cocoa

@main
class AppDelegate: NSObject, NSApplicationDelegate {

    private var permittedRoot: URL?

    func application(_ application: NSApplication, open urls: [URL]) {
        // Refuse batches: one explicit link selects one file.
        guard urls.count == 1, let url = urls.first else {
            showRevealFailure(.invalidURL)
            return
        }
        do {
            let request = try FinderReveal.Request(url)
            let map = try FinderReveal.load(request)
            let root = URL(fileURLWithPath: map.root, isDirectory: true)
            if permittedRoot != root {
                // Reuse the existing user-selected read-only entitlement. No new
                // filesystem grants, persistent bookmarks, or browser permissions.
                NSApp.activate(ignoringOtherApps: true)
                let panel = NSOpenPanel()
                panel.title = "Allow OCDJ to reveal indexed tracks"
                panel.message = "Select the library root used for your local snapshot. Access lasts while this helper is open."
                panel.prompt = "Allow library"
                panel.canChooseFiles = false
                panel.canChooseDirectories = true
                panel.allowsMultipleSelection = false
                panel.directoryURL = root
                guard panel.runModal() == .OK, panel.url?.path == map.root else {
                    showRevealFailure(.staleFile)
                    return
                }
                permittedRoot = root
            }
            let target = try FinderReveal.target(request, in: map)
            NSWorkspace.shared.activateFileViewerSelecting([target])
        } catch let failure as FinderReveal.Failure {
            showRevealFailure(failure)
        } catch {
            showRevealFailure(.staleMap)
        }
    }

    private func showRevealFailure(_ failure: FinderReveal.Failure) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "OCDJ could not reveal this track"
        alert.informativeText = failure.message
        alert.runModal()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Override point for customization after application launch.
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

}
