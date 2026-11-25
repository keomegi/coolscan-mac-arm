// Coolscan.app: the Coolscan page in its own window, with its own Dock icon.
//
// It starts `.venv/bin/python coolscan.py --window` in the project folder (the folder is written
// into the app's Info.plist by mac/build_app.sh), shows the page in a window, and when the window
// is closed or the app quits, it stops that Python process. Python also watches its input: if
// this app ends in any way, even a crash, the input closes and the server stops by itself, so
// nothing is ever left running on the port.
//
// Build it with:  mac/build_app.sh   (needs the Xcode Command Line Tools for swiftc)

import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var serverInput: Pipe?
    var baseURL: URL?
    var output = ""
    var lineBuffer = ""
    var quitting = false
    var titleWatch: NSKeyValueObservation?
    var logFile: FileHandle?

    // MARK: start

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    var folder: URL? {
        guard let path = Bundle.main.object(forInfoDictionaryKey: "CoolscanFolder") as? String,
              !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()   // keeps the page's own small settings between runs
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        if #available(macOS 13.3, *) { webView.isInspectable = true }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Coolscan"
        window.minSize = NSSize(width: 900, height: 600)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("Coolscan main window")
        window.makeKeyAndOrderFront(nil)
        titleWatch = webView.observe(\.title, options: [.new]) { [weak self] view, _ in
            DispatchQueue.main.async {
                let t = view.title ?? ""
                self?.window.title = t.isEmpty ? "Coolscan" : t
            }
        }
        showMessage("Starting Coolscan…", detail: "Connecting to the scanner.")
    }

    func showMessage(_ title: String, detail: String) {
        func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        }
        let html = """
        <html><head><meta charset="utf-8"><style>
        :root { color-scheme: light dark; }
        body { font: 15px -apple-system, sans-serif; display: flex; align-items: center; justify-content: center;
               height: 100vh; margin: 0; background: Canvas; color: CanvasText; }
        div { max-width: 640px; text-align: center; } pre { text-align: left; white-space: pre-wrap; opacity: .8; }
        </style></head><body><div><h2>\(esc(title))</h2><pre>\(esc(detail))</pre></div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func startServer() {
        guard let folder = folder else {
            fail("Coolscan does not know where its folder is.",
                 detail: "Build the app again with mac/build_app.sh from the Coolscan folder.")
            return
        }
        let python = folder.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            fail("Python was not found in \(folder.path)/.venv",
                 detail: "Make the environment there first (python3 -m venv .venv, then pip install -r requirements.txt).")
            return
        }
        openLog()
        let p = Process()
        p.executableURL = python
        var args = ["-u", "coolscan.py", "--window"]
        if let extra = Bundle.main.object(forInfoDictionaryKey: "CoolscanArguments") as? [String] { args += extra }
        p.arguments = args
        p.currentDirectoryURL = folder
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["VIRTUAL_ENV"] = folder.appendingPathComponent(".venv").path
        env["PATH"] = folder.appendingPathComponent(".venv/bin").path + ":" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        p.environment = env

        let input = Pipe()
        let out = Pipe()
        p.standardInput = input
        p.standardOutput = out
        p.standardError = out
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { h.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.took(text) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverEnded(proc.terminationStatus) }
        }
        do {
            try p.run()
        } catch {
            fail("Coolscan could not start.", detail: error.localizedDescription)
            return
        }
        server = p
        serverInput = input
    }

    func openLog() {
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
        let url = logs.appendingPathComponent("Coolscan.log")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        // keep one run only: the log is for "what did it say before it stopped"
        FileManager.default.createFile(atPath: url.path, contents: nil)
        logFile = try? FileHandle(forWritingTo: url)
    }

    func took(_ text: String) {
        logFile?.write(Data(text.utf8))
        output += text
        if output.count > 20000 { output = String(output.suffix(10000)) }
        lineBuffer += text
        while let nl = lineBuffer.firstIndex(of: "\n") {
            let line = String(lineBuffer[..<nl])
            lineBuffer = String(lineBuffer[lineBuffer.index(after: nl)...])
            if line.hasPrefix("COOLSCAN_URL "), baseURL == nil,
               let url = URL(string: String(line.dropFirst("COOLSCAN_URL ".count)).trimmingCharacters(in: .whitespaces)) {
                baseURL = url
                webView.load(URLRequest(url: url))
            }
        }
    }

    func serverEnded(_ status: Int32) {
        server = nil
        if quitting { return }
        let lastLines = output.split(separator: "\n", omittingEmptySubsequences: false).suffix(25).joined(separator: "\n")
        fail("Coolscan stopped.", detail: lastLines.isEmpty ? "It ended with code \(status)." : lastLines)
    }

    func fail(_ title: String, detail: String) {
        showMessage(title, detail: detail)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = String(detail.suffix(1500))
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Keep the window")
        if alert.runModal() == .alertFirstButtonReturn {
            quitting = true
            NSApp.terminate(nil)
        }
    }

    // MARK: quitting

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Closing the window quits the app (and so the server); a running scan is asked about first.
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting || baseURL == nil || server == nil {
            quitting = true
            stopServer()
            return .terminateNow
        }
        runningJob { job in
            if let job = job {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "Coolscan is still busy (\(job))."
                alert.informativeText = "If you quit now, it stops after the current step and the scan is not finished."
                alert.addButton(withTitle: "Keep it running")
                alert.addButton(withTitle: "Quit anyway")
                if alert.runModal() == .alertFirstButtonReturn {
                    NSApp.reply(toApplicationShouldTerminate: false)
                    return
                }
            }
            self.quitting = true
            self.stopServer()
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    /// The name of the job the app is doing, or nil when it is idle (or cannot be asked).
    func runningJob(_ done: @escaping (String?) -> Void) {
        guard let base = baseURL else { done(nil); return }
        var req = URLRequest(url: base.appendingPathComponent("api/state"))
        req.timeoutInterval = 3
        URLSession.shared.dataTask(with: req) { data, _, _ in
            var job: String?
            if let data = data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let j = obj["job"], !(j is NSNull) {
                job = "\(j)"
            }
            DispatchQueue.main.async { done(job) }
        }.resume()
    }

    func stopServer() {
        guard let p = server else { return }
        p.terminationHandler = nil
        // Closing Python's input is the polite way: it stops a running job, lets go of the
        // scanner and the port, and ends. If it does not, it is told, then made to.
        try? serverInput?.fileHandleForWriting.close()
        if !waitFor(p, seconds: 8) {
            p.terminate()
            if !waitFor(p, seconds: 2) { kill(p.processIdentifier, SIGKILL) }
        }
        server = nil
        try? logFile?.close()
    }

    func waitFor(_ p: Process, seconds: Double) -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while p.isRunning && Date() < end { usleep(100_000) }
        return !p.isRunning
    }

    // MARK: the page

    func isOurs(_ url: URL?) -> Bool {
        guard let url = url else { return true }
        if url.scheme == "about" || url.scheme == "data" || url.scheme == "blob" { return true }
        return url.host == "127.0.0.1" || url.host == "localhost"
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Links to other sites (help pages and the like) open in the usual browser.
        if let url = action.request.url, !isOurs(url) {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // A link meant for a new window: ours open here, others in the browser.
        if let url = action.request.url {
            if isOurs(url) { webView.load(URLRequest(url: url)) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    // MARK: menus

    @objc func reloadPage(_ sender: Any?) { webView.reload() }
    @objc func openInBrowser(_ sender: Any?) {
        if let url = webView.url, isOurs(url), url.scheme == "http" { NSWorkspace.shared.open(url) }
    }
    @objc func showLog(_ sender: Any?) {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Coolscan.log")
        NSWorkspace.shared.open(url)
    }
    @objc func showFolder(_ sender: Any?) {
        if let f = folder { NSWorkspace.shared.open(f) }
    }
    @objc func zoomIn(_ sender: Any?) { webView.pageZoom = min(webView.pageZoom + 0.1, 2.0) }
    @objc func zoomOut(_ sender: Any?) { webView.pageZoom = max(webView.pageZoom - 0.1, 0.5) }
    @objc func zoomReset(_ sender: Any?) { webView.pageZoom = 1.0 }

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Coolscan", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Coolscan", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Coolscan", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload Page", action: #selector(reloadPage(_:)), keyEquivalent: "r")
        view.addItem(.separator())
        view.addItem(withTitle: "Actual Size", action: #selector(zoomReset(_:)), keyEquivalent: "0")
        view.addItem(withTitle: "Zoom In", action: #selector(zoomIn(_:)), keyEquivalent: "+")
        view.addItem(withTitle: "Zoom Out", action: #selector(zoomOut(_:)), keyEquivalent: "-")
        view.addItem(.separator())
        view.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser(_:)), keyEquivalent: "")
        view.addItem(withTitle: "Show Coolscan Folder", action: #selector(showFolder(_:)), keyEquivalent: "")
        view.addItem(withTitle: "Show Start-up Log", action: #selector(showLog(_:)), keyEquivalent: "")
        for item in view.items where item.action != nil { item.target = self }
        viewItem.submenu = view
        main.addItem(viewItem)

        let winItem = NSMenuItem()
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        main.addItem(winItem)
        NSApp.windowsMenu = win

        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
