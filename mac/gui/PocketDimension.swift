import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct Device: Codable, Identifiable, Hashable {
    let vid: Int
    let pid: Int
    let bus: Int?
    let address: Int?
    let name: String
    var serial: String? = nil
    var device_id: String? = nil
    var displayName: String {
        let identity = serial.map { "Serial " + $0 } ?? device_id.map { "Phone ID " + String($0.suffix(12)) }
        return name + (identity.map { " · " + $0 } ?? "") + " · USB \(bus ?? 0):\(address ?? 0)"
    }
    var id: String { "\(vid):\(pid):\(bus ?? -1):\(address ?? -1)" }
    var arguments: [String] {
        var values: [String] = []
        if let bus { values += ["--bus", String(bus)] }
        if let address { values += ["--address", String(address)] }
        return values
    }
}
struct PairingInfo: Decodable {
    let event: String
    let phone_fingerprint: String
    let host_fingerprint: String
    let host_name: String
    let reason: String
    let device: Device?
    var valid: Bool { event == "pairing_required" && [phone_fingerprint, host_fingerprint].allSatisfy { $0.count == 64 && $0.allSatisfy { "0123456789abcdef".contains($0) } } }
    static func formatted(_ value: String) -> String { stride(from: 0, to: value.count, by: 8).map { offset in String(value.dropFirst(offset).prefix(8)) }.joined(separator: " ") }
}
struct PhoneStatus: Decodable {
    let selected: Bool
    let name: String?
    let type: String?
    let writable: Bool?
    let device: Device?
    let device_id: String?
    let scope_id: String?
}
struct Entry: Codable, Identifiable {
    let name: String
    let type: String
    let size: Int64?
    let path: String
    var id: String { path }
    var modified: Int64? = nil
    var sortSize: Int64 { size ?? -1 }
    var sortModified: Int64 { modified ?? 0 }
    var folder: Bool { type == "directory" }
}
struct TransferProgress: Decodable {
    let event: String
    let name: String
    let bytes: Int64
    let total: Int64?
    var bytes_per_second: Double? = nil
}
struct TransferJob: Codable {
    let name: String
    let arguments: [String]
    var localDestination: URL? = nil
    var deviceID: String? = nil
    var scopeID: String? = nil
    var expectedBytes: Int64? = nil
}

enum ConflictPolicy: String, CaseIterable, Identifiable {
    case error, skip, keepBoth = "keep-both"
    var id: String { rawValue }
    var title: String {
        switch self { case .error: return "Stop"; case .skip: return "Skip"; case .keepBoth: return "Keep Both" }
    }
}
enum QueueStatus: String, Codable { case waiting = "Waiting", running = "Transferring", completed = "Completed", skipped = "Skipped", failed = "Failed", cancelled = "Cancelled" }
struct QueuedTransfer: Identifiable, Codable {
    var id = UUID()
    var job: TransferJob
    var status: QueueStatus = .waiting
    var detail = ""
    var location: URL? = nil
    var transferredBytes: Int64 = 0
}

enum BrowserError: LocalizedError {
    case unsafeName, duplicateName, existingDestination(String)
    var errorDescription: String? {
        switch self {
        case .unsafeName: return "This item has an unsafe local filename. Choose a different destination name."
        case .duplicateName: return "Selected names would collide on the Mac. Download these items separately with different names."
        case .existingDestination(let name): return "Destination already exists: \(name). Choose another folder or filename."
        }
    }
}

func safeLocalName(_ name: String) throws -> String {
    guard !name.isEmpty, name != ".", name != "..", !name.contains("/"), !name.contains("\\"),
          !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { throw BrowserError.unsafeName }
    return name
}

func downloadDestinations(entries: [Entry], parent: URL) throws -> [URL] {
    var names = Set<String>()
    return try entries.map { entry in
        let name = try safeLocalName(entry.name)
        let key = name.precomposedStringWithCanonicalMapping.lowercased()
        guard names.insert(key).inserted else { throw BrowserError.duplicateName }
        let target = parent.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: target.path) || (try? FileManager.default.destinationOfSymbolicLink(atPath: target.path)) != nil {
            throw BrowserError.existingDestination(name)
        }
        return target
    }
}

final class PipeCollector: @unchecked Sendable {
    let lock = NSLock()
    var data = Data()
    func collect(_ pipe: Pipe, progress: ((TransferProgress) -> Void)? = nil) {
        var pending = Data()
        while true {
            let chunk = pipe.fileHandleForReading.availableData
            if chunk.isEmpty { break }
            lock.lock(); data.append(chunk); lock.unlock()
            if let progress {
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 10) {
                    let line = Data(pending[..<newline])
                    pending.removeSubrange(...newline)
                    if let value = try? JSONDecoder().decode(TransferProgress.self, from: line) { progress(value) }
                }
            }
        }
    }
}

@MainActor final class Model: ObservableObject {
    @Published var devices: [Device] = []
    @Published var deviceID = ""
    @Published var entries: [Entry] = []
    @Published var selection = Set<Entry.ID>()
    @Published private(set) var remote = ""
    @Published var pathInput = ""
    @Published private(set) var backHistory: [String] = []
    @Published private(set) var forwardHistory: [String] = []
    @Published var search = ""
    @Published var batchIndex = 0
    @Published var batchCount = 0
    @Published var conflictPolicy: ConflictPolicy = .keepBoth
    @Published var queue: [QueuedTransfer] = [] { didSet { scheduleHistorySave() } }
    @Published var cancelling = false
    @Published var planning = false
    @Published var showQueue = false
    @Published var showPairing = false
    @Published var pairingInfo: PairingInfo?
    @Published var pairingStatus = ""
    @Published var resumeEnabled = true
    @Published var sortOrder = [KeyPathComparator(\Entry.name)]
    @Published var dropTargeted = false
    @Published var transferRate: Double = 0
    private var batchIDs: [UUID] = []
    private var currentFiles: [String: Int64] = [:]
    private var historyURL: URL?
    private var historySave: DispatchWorkItem?
    var remainingCount: Int { queue.filter { batchIDs.contains($0.id) && [.waiting, .running, .failed, .cancelled].contains($0.status) }.count }
    var overallBytes: Int64 { queue.filter { batchIDs.contains($0.id) && $0.status != .skipped }.reduce(0) { $0 + $1.transferredBytes } }
    var overallTotal: Int64? {
        let items = queue.filter { batchIDs.contains($0.id) && $0.status != .skipped }
        guard !items.isEmpty, items.allSatisfy({ $0.job.expectedBytes != nil }) else { return nil }
        return items.reduce(0) { $0 + max($1.job.expectedBytes ?? 0, $1.transferredBytes) }
    }
    var etaText: String {
        guard let total = overallTotal, transferRate > 0, busy else { return "Estimating…" }
        let seconds = max(0, Double(total - overallBytes) / transferRate)
        if seconds < 60 { return "About \(Int(seconds))s remaining" }
        if seconds < 3600 { return "About \(Int(seconds / 60))m remaining" }
        return "About \(Int(seconds / 3600))h \(Int(seconds.truncatingRemainder(dividingBy: 3600) / 60))m remaining"
    }
    private var process: Process?
    private enum QueueAction { case cancel, skip }
    private var requestedAction: QueueAction?
    private var connectedDeviceID: String?
    private var connectedScopeID: String?
    private var currentQueueID: UUID?
    var hasUnfinished: Bool { queue.contains { [.waiting, .failed, .cancelled].contains($0.status) } }
    var hasFailed: Bool { queue.contains { $0.status == .failed || $0.status == .cancelled } }
    @Published var rootName = "No folder shared"
    @Published var writable = false
    @Published var rootIsFolder = false
    @Published var shared = false
    @Published var busy = false
    @Published var message = "Connect your phone and open Pocket Dimension."
    @Published var progress: TransferProgress?
    private var phone: Device? { devices.first { $0.id == deviceID } }
    var visibleEntries: [Entry] {
        let sorted = entries.filter { search.isEmpty || $0.name.localizedStandardContains(search) }.sorted(using: sortOrder)
        return sorted.filter(\.folder) + sorted.filter { !$0.folder }
    }
    var selectedEntries: [Entry] { visibleEntries.filter { selection.contains($0.id) } }
    var selectedFolder: Entry? { selectedEntries.count == 1 && selectedEntries[0].folder ? selectedEntries[0] : nil }
    let executable: URL
    init(executable: URL? = nil, historyURL: URL? = nil) {
        self.executable = executable ?? Bundle.main.resourceURL!.appendingPathComponent("cli/pocket-dimension-gui")
        self.historyURL = historyURL ?? (executable == nil ? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("PocketDimension/history.json") : nil)
        if let url = self.historyURL, let data = try? Data(contentsOf: url), let saved = try? JSONDecoder().decode([QueuedTransfer].self, from: data) {
            self.queue = saved.suffix(200).map { item in
                var restored = item
                if restored.status == .running { restored.status = .waiting; restored.detail = "Interrupted · ready to retry or resume" }
                return restored
            }
        }
    }
    private func scheduleHistorySave() {
        guard historyURL != nil else { return }
        historySave?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.persistHistory() }
        historySave = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)
    }
    func persistHistory() {
        guard let url = historyURL else { return }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let data = try JSONEncoder().encode(Array(queue.suffix(200)))
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch { message = "Transfer history could not be saved: " + error.localizedDescription }
    }
    private func updateProgress(_ value: TransferProgress) {
        progress = value
        transferRate = max(0, value.bytes_per_second ?? 0)
        currentFiles[value.name] = max(currentFiles[value.name] ?? 0, value.bytes)
        if let id = currentQueueID, let index = queue.firstIndex(where: { $0.id == id }) {
            queue[index].transferredBytes = currentFiles.values.reduce(0, +)
        }
    }
    func selectAll() { selection = Set(visibleEntries.map(\.id)) }
    func primaryAction(_ ids: Set<Entry.ID>) {
        guard ids.count == 1, let entry = entries.first(where: { ids.contains($0.id) }), entry.folder else { return }
        enter(entry)
    }

    func run(_ args: [String], failed: ((String, Int32) -> Void)? = nil, completed: @escaping (Data) -> Void) {
        guard !busy else { return }
        busy = true
        progress = nil
        let url = executable
        let child = Process()
        self.process = child
        DispatchQueue.global(qos: .userInitiated).async {
            let process = child
            let stdout = Pipe(), stderr = Pipe()
            process.executableURL = url
            process.arguments = args
            process.standardOutput = stdout
            process.standardError = stderr
            process.standardInput = FileHandle.nullDevice
            let output = PipeCollector(), errors = PipeCollector()
            do {
                try process.run()
                DispatchQueue.main.async {
                    if self.process === process && self.requestedAction != nil { process.interrupt() }
                }
                let readers = DispatchGroup()
                readers.enter()
                DispatchQueue.global().async { output.collect(stdout); readers.leave() }
                readers.enter()
                DispatchQueue.global().async {
                    errors.collect(stderr) { value in
                        DispatchQueue.main.async { self.updateProgress(value) }
                    }
                    readers.leave()
                }
                process.waitUntilExit()
                readers.wait()
                let error = String(data: errors.data, encoding: .utf8) ?? "Unknown error"
                DispatchQueue.main.async {
                    self.busy = false
                    self.process = nil
                    if process.terminationStatus == 0 { completed(output.data) }
                    else {
                        self.progress = nil
                        if let info = error.split(separator: "\n").compactMap({ try? JSONDecoder().decode(PairingInfo.self, from: Data($0.utf8)) }).first(where: { $0.valid }) {
                            self.pairingInfo = info; self.pairingStatus = info.reason; self.showPairing = true
                            if let device = info.device { self.devices.removeAll { $0.id == device.id }; self.devices.append(device); self.deviceID = device.id }
                        }
                        self.message = error.split(separator: "\n").filter { !$0.hasPrefix("{") }.joined(separator: "\n")
                        if self.message.isEmpty { self.message = "Transfer failed. Reconnect your phone and retry." }
                        failed?(self.message, process.terminationStatus)
                    }
                }
            } catch {
                DispatchQueue.main.async { self.busy = false; self.process = nil; self.message = error.localizedDescription; failed?(self.message, -1) }
            }
        }
    }
    func refreshDevices() {
        message = "Looking for your phone…"
        run(["devices", "--json", "--details"]) { data in
            guard let values = try? JSONDecoder().decode([Device].self, from: data) else { self.message = "Could not read USB devices."; return }
            self.devices = values
            if !values.contains(where: { $0.id == self.deviceID }) { self.deviceID = values.count == 1 ? values[0].id : "" }
            self.message = values.isEmpty ? "No recognized phone. Unlock it and select File transfer in USB settings." : "Select your phone and click Connect."
        }
    }
    func pairComputer() {
        guard !busy, let info = pairingInfo, info.valid else { return }
        pairingStatus = "Compare both fingerprints with the phone’s approval screen, then approve on the phone."
        run(["pair", "--phone-fingerprint", info.phone_fingerprint, "--timeout", "90"] + (info.device?.arguments ?? phone?.arguments ?? []), failed: { error, _ in self.pairingStatus = error }) { _ in
            self.pairingInfo = nil; self.showPairing = false; self.connect()
        }
    }
    func cancelPairing() {
        if busy { process?.interrupt() }
        showPairing = false; pairingInfo = nil
    }
    func forgetPhone() {
        guard !busy else { return }
        run(["unpair"] + (phone?.arguments ?? [])) { _ in
            self.shared = false; self.entries = []; self.selection = []; self.message = "Computer trust revoked on the phone."
        }
    }
    func connect() {
        message = "Connecting… approve the accessory prompt on your phone."
        let args = ["connect", "--json"] + (phone?.arguments ?? [])
        run(args) { data in
            guard let state = try? JSONDecoder().decode(PhoneStatus.self, from: data) else { self.message = "Could not read phone status."; return }
            if let device = state.device {
                self.devices.removeAll { $0.id == self.deviceID || $0.id == device.id }
                self.devices.append(device); self.deviceID = device.id
            }
            self.connectedDeviceID = state.device_id; self.connectedScopeID = state.scope_id
            self.rootName = state.name ?? "No selection"
            self.rootIsFolder = state.type == "directory"
            self.shared = state.selected
            self.writable = state.type == "directory" && state.writable == true
            self.remote = ""; self.pathInput = ""
            self.backHistory = []; self.forwardHistory = []; self.search = ""
            self.entries = []; self.selection = []
            if state.selected { self.browse() }
            else { self.message = "Connected. Choose a file, folder, or device storage in the Android app, then click Connect again." }
        }
    }
    enum Navigation { case refresh, push, back, forward }
    func browse(path: String? = nil, navigation: Navigation = .refresh, successMessage: String? = nil) {
        guard !busy, shared else { return }
        let target = path ?? remote
        let previous = remote
        message = "Reading \(target.isEmpty ? rootName : target)…"
        run(["ls", "--json", "--remote", target] + (phone?.arguments ?? [])) { data in
            guard let values = try? JSONDecoder().decode([Entry].self, from: data) else { self.message = "Could not read folder listing."; return }
            if target != previous {
                switch navigation {
                case .push: self.backHistory.append(previous); self.forwardHistory = []
                case .back: self.backHistory.removeLast(); self.forwardHistory.append(previous)
                case .forward: self.forwardHistory.removeLast(); self.backHistory.append(previous)
                case .refresh: break
                }
                self.selection = []; self.search = ""
            }
            self.remote = target; self.pathInput = target
            self.entries = values
            self.selection.formIntersection(Set(self.visibleEntries.map(\.id)))
            self.message = successMessage ?? "\(values.count) items · \(self.writable ? "Read and write access" : "Read access")"
        }
    }
    func refresh() { browse() }
    func go() { browse(path: pathInput, navigation: .push) }
    func enter(_ entry: Entry) {
        guard entry.folder else { return }
        browse(path: entry.path, navigation: .push)
    }
    func up() {
        guard !remote.isEmpty else { return }
        browse(path: remote.split(separator: "/").dropLast().joined(separator: "/"), navigation: .push)
    }
    func back() {
        if let target = backHistory.last { browse(path: target, navigation: .back) }
    }
    func forward() {
        if let target = forwardHistory.last { browse(path: target, navigation: .forward) }
    }
    func runTransfers(_ jobs: [TransferJob], finished: @escaping () -> Void) {
        guard !busy, !jobs.isEmpty else { return }
        // Keep a session history, trimming only old terminal rows.
        if queue.count > 200 { queue.removeAll { [.completed, .skipped].contains($0.status) } }
        let added = jobs.map { source -> QueuedTransfer in
            var job = source
            job.deviceID = connectedDeviceID; job.scopeID = connectedScopeID
            return QueuedTransfer(job: job, location: job.localDestination)
        }
        queue += added
        persistHistory()
        prepareQueue(added.map(\.id), finished: finished)
    }
    private func prepareQueue(_ ids: [UUID], finished: @escaping () -> Void) {
        guard !ids.isEmpty else { return }
        batchIDs = ids; batchCount = ids.count; planning = true
        requestedAction = nil; cancelling = false
        func prepare(_ index: Int) {
            if self.requestedAction == .cancel {
                self.planning = false; self.batchCount = 0; self.currentQueueID = nil
                self.cancelling = false; self.requestedAction = nil; self.persistHistory()
                self.message = "Queue stopped before transferring. Items are saved for retry."; return
            }
            if index == ids.count { self.planning = false; self.runQueue(ids, finished: finished); return }
            guard let item = self.queue.first(where: { $0.id == ids[index] }) else { return }
            if item.job.expectedBytes != nil || !["pull", "push"].contains(item.job.arguments.first ?? "") { prepare(index + 1); return }
            let args: [String]
            if item.job.arguments.first == "push", item.job.arguments.count > 1 {
                args = ["local-plan", item.job.arguments[1]]
            } else {
                let transferArgs = self.arguments(for: item.job)
                let path = transferArgs.firstIndex(of: "--remote").flatMap { $0 + 1 < transferArgs.count ? transferArgs[$0 + 1] : nil } ?? ""
                var binding: [String] = []
                for option in ["--bus", "--address", "--device-id", "--scope-id"] {
                    if let offset = transferArgs.firstIndex(of: option), offset + 1 < transferArgs.count { binding += [option, transferArgs[offset + 1]] }
                }
                args = ["plan", "--json", "--remote", path] + binding
            }
            self.currentQueueID = item.id
            self.message = "Calculating transfer size: " + item.job.name
            self.run(args, failed: { _, _ in prepare(index + 1) }) { data in
                if let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], object["known"] as? Bool == true,
                   let bytes = object["bytes"] as? Int64, let row = self.queue.firstIndex(where: { $0.id == item.id }) {
                    self.queue[row].job.expectedBytes = bytes
                }
                prepare(index + 1)
            }
        }
        prepare(0)
    }
    private func updateQueue(_ id: UUID, status: QueueStatus, detail: String = "", result: Data? = nil) {
        guard let index = queue.firstIndex(where: { $0.id == id }) else { return }
        queue[index].status = status; queue[index].detail = detail
        if let result, let object = (try? JSONSerialization.jsonObject(with: result)) as? [String: Any] {
            if let bytes = object["bytes"] as? Int64 { queue[index].transferredBytes = bytes; queue[index].job.expectedBytes = bytes }
            if object["status"] as? String == "skipped" { queue[index].status = .skipped }
            if let destination = object["destination"] as? String { queue[index].location = URL(fileURLWithPath: destination) }
            if let skipped = object["skipped"] as? Int, skipped > 0 { queue[index].detail = "\(skipped) existing item(s) skipped" }
        }
        persistHistory()
    }
    private func runQueue(_ ids: [UUID], finished: @escaping () -> Void) {
        guard !busy, !ids.isEmpty else { return }
        requestedAction = nil; cancelling = false
        batchCount = ids.count
        batchIDs = ids
        func stop() {
            self.batchIndex = 0; self.batchCount = 0; self.currentQueueID = nil
            self.cancelling = false; self.requestedAction = nil; self.progress = nil; self.transferRate = 0; self.persistHistory()
        }
        func next(_ index: Int) {
            if index == ids.count {
                stop(); finished(); return
            }
            guard let item = self.queue.first(where: { $0.id == ids[index] }) else { return }
            self.currentQueueID = item.id
            self.currentFiles = [:]
            self.batchIndex = index + 1
            self.message = "Item \(index + 1) of \(ids.count): \(item.job.name)"
            self.updateQueue(item.id, status: .running)
            self.run(self.arguments(for: item.job), failed: { error, code in
                let action = self.requestedAction
                if action == .skip && code == 130 {
                    self.updateQueue(item.id, status: .skipped, detail: "Skipped by you")
                    self.requestedAction = nil; self.cancelling = false
                    next(index + 1)
                } else {
                    self.updateQueue(item.id, status: code == 130 || action == .cancel ? .cancelled : .failed, detail: error)
                    stop()
                    self.message = "Stopped after \(index) of \(ids.count) completed items. Completed transfers are kept.\n" + error
                }
            }) { result in
                self.updateQueue(item.id, status: .completed, result: result)
                if self.requestedAction == .cancel {
                    stop(); self.message = "Queue stopped. The current item finished verification and was kept."
                } else {
                    self.requestedAction = nil; self.cancelling = false
                    next(index + 1)
                }
            }
        }
        next(0)
    }
    private func arguments(for job: TransferJob) -> [String] {
        guard ["pull", "push"].contains(job.arguments.first ?? "") else { return job.arguments }
        var args = job.arguments
        if let deviceID = job.deviceID {
            // Rebind a new USB address only after Connect identifies the same phone.
            if deviceID == connectedDeviceID, let phone {
                var filtered: [String] = []; var index = 0
                while index < args.count {
                    if ["--bus", "--address"].contains(args[index]) { index += 2 }
                    else { filtered.append(args[index]); index += 1 }
                }
                args = filtered + phone.arguments
            }
            args += ["--device-id", deviceID]
        }
        if let scopeID = job.scopeID { args += ["--scope-id", scopeID] }
        if resumeEnabled && !args.contains("--resume") { args.append("--resume") }
        return args
    }
    func cancelQueue() {
        guard busy, currentQueueID != nil, !cancelling else { return }
        requestedAction = .cancel; cancelling = true
        message = "Cancelling safely… waiting for the current USB frame. Older APKs may need to finish streaming this file."
        if process?.isRunning == true { process?.interrupt() }
    }
    func skipCurrent() {
        guard busy, !planning, currentQueueID != nil, !cancelling else { return }
        requestedAction = .skip; cancelling = true
        message = "Skipping safely… waiting for the current USB frame."
        if process?.isRunning == true { process?.interrupt() }
    }
    func retryUnfinished() {
        guard !busy else { return }
        let ids = queue.filter { [.waiting, .failed, .cancelled].contains($0.status) }.map(\.id)
        prepareQueue(ids) { self.message = "Queue finished · completed items verified." }
    }
    func skipFailedAndContinue() {
        guard !busy else { return }
        for index in queue.indices where queue[index].status == .failed || queue[index].status == .cancelled {
            queue[index].status = .skipped
        }
        let ids = queue.filter { $0.status == .waiting }.map(\.id)
        if ids.isEmpty { message = "Failed items skipped."; return }
        runQueue(ids) { self.message = "Queue finished · failed items skipped." }
    }
    func clearFinished() {
        guard !busy else { return }
        queue.removeAll { [.completed, .skipped].contains($0.status) }
    }
    func reveal(_ item: QueuedTransfer) {
        if let url = item.location, item.status == .completed { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }
    func pull(ids: Set<Entry.ID>? = nil) {
        guard !busy else { return }
        let items = ids.map { ids in entries.filter { ids.contains($0.id) } } ?? selectedEntries
        let phoneArguments = phone?.arguments ?? []
        if items.count == 1 && !items[0].folder || (items.isEmpty && !rootIsFolder) {
            let entry = items.first
            let panel = NSSavePanel()
            panel.title = "Save from phone"
            panel.nameFieldStringValue = (try? safeLocalName(entry?.name ?? rootName)) ?? "Pocket Dimension download"
            guard panel.runModal() == .OK, let url = panel.url else { return }
            if conflictPolicy == .error && (FileManager.default.fileExists(atPath: url.path) || (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil) {
                message = BrowserError.existingDestination(url.lastPathComponent).localizedDescription; return
            }
            let path = entry?.path ?? remote
            runTransfers([TransferJob(name: entry?.name ?? rootName, arguments: ["pull", url.path, "--remote", path, "--json", "--json-progress", "--conflict", conflictPolicy.rawValue] + phoneArguments, localDestination: url)]) {
                self.message = "Saved to \(url.path) · checksum verified."
            }
            return
        }
        let panel = NSOpenPanel()
        panel.title = items.count > 1 ? "Download \(items.count) selected items to…" : "Choose a parent folder for the download"
        panel.prompt = "Download here"
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let parent = panel.url else { return }
        let targets: [Entry]
        if items.isEmpty {
            let name = remote.split(separator: "/").last.map(String.init) ?? rootName
            targets = [Entry(name: (try? safeLocalName(name)) ?? "Pocket Dimension download", type: "directory", size: nil, path: remote)]
        } else { targets = items }
        do {
            let destinations = conflictPolicy == .error
                ? try downloadDestinations(entries: targets, parent: parent)
                : try targets.map { parent.appendingPathComponent(try safeLocalName($0.name)) }
            let jobs = zip(targets, destinations).map { entry, destination in
                TransferJob(name: entry.name, arguments: ["pull", destination.path, "--remote", entry.path, "--json", "--json-progress", "--conflict", conflictPolicy.rawValue] + phoneArguments, localDestination: destination)
            }
            runTransfers(jobs) { self.message = "Downloaded \(targets.count) item(s) to \(parent.path) · checksums verified." }
        } catch { message = error.localizedDescription }
    }
    func push() {
        guard !busy else { return }
        let panel = NSOpenPanel()
        panel.title = "Upload files or folders"
        panel.prompt = "Upload"
        panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.allowsMultipleSelection = true
        guard panel.runModal() == .OK, !panel.urls.isEmpty else { return }
        enqueueUploads(panel.urls)
    }
    func enqueueUploads(_ sourceURLs: [URL]) {
        guard !busy, shared, writable, !sourceURLs.isEmpty else { return }
        var names = Set<String>()
        do {
            for source in sourceURLs {
                let name = try safeLocalName(source.lastPathComponent)
                guard names.insert(name).inserted else { throw BrowserError.duplicateName }
            }
        } catch { message = error.localizedDescription; return }
        let phoneArguments = phone?.arguments ?? []
        let jobs = sourceURLs.map { source in
            let path = remote.isEmpty ? source.lastPathComponent : remote + "/" + source.lastPathComponent
            return TransferJob(name: source.lastPathComponent, arguments: ["push", source.path, "--remote", path, "--json", "--json-progress", "--conflict", conflictPolicy.rawValue] + phoneArguments)
        }
        runTransfers(jobs) { self.browse(successMessage: "Uploaded \(sourceURLs.count) item(s) · checksums verified.") }
    }
    func receiveDrop(_ providers: [NSItemProvider]) -> Bool {
        guard !busy, shared, writable else { return false }
        let dropRemote = remote, dropDevice = connectedDeviceID, dropScope = connectedScopeID
        let supported = providers.enumerated().filter { $0.element.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
        guard !supported.isEmpty else { return false }
        let group = DispatchGroup(), lock = NSLock()
        var urls: [(Int, URL)] = []
        for (index, provider) in supported {
            group.enter()
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { value, _ in
                defer { group.leave() }
                let url: URL?
                if let data = value as? Data { url = URL(dataRepresentation: data, relativeTo: nil) }
                else { url = value as? URL }
                if let url, url.isFileURL { lock.lock(); urls.append((index, url)); lock.unlock() }
            }
        }
        group.notify(queue: .main) {
            guard self.remote == dropRemote, self.connectedDeviceID == dropDevice, self.connectedScopeID == dropScope else { self.message = "Drop cancelled because the phone or folder changed."; return }
            var seen = Set<URL>()
            let ordered = urls.sorted { $0.0 < $1.0 }.map(\.1).filter { seen.insert($0.standardizedFileURL).inserted }
            self.enqueueUploads(ordered)
        }
        return true
    }

}

enum PortalTheme {
    static let blue = Color(red: 0.20, green: 0.43, blue: 0.98)
    static let cyan = Color(red: 0.24, green: 0.78, blue: 1.0)
}

struct PortalMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 18).fill(Color(red: 0.035, green: 0.07, blue: 0.17))
            Ellipse().fill(PortalTheme.blue.opacity(0.3)).frame(width: 38, height: 52).blur(radius: 7)
            Ellipse().stroke(LinearGradient(colors: [PortalTheme.cyan, PortalTheme.blue, PortalTheme.cyan], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 3).frame(width: 31, height: 46).rotationEffect(.degrees(14))
            Ellipse().stroke(PortalTheme.cyan.opacity(0.65), lineWidth: 1).frame(width: 20, height: 34).rotationEffect(.degrees(14))
            Image(systemName: "sparkle").font(.system(size: 9)).foregroundStyle(.white).offset(x: 19, y: -20)
        }.frame(width: 64, height: 64).accessibilityHidden(true)
    }
}

struct ContentView: View {
    @StateObject private var model: Model
    init(model: Model? = nil) { _model = StateObject(wrappedValue: model ?? Model()) }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                PortalMark()
                VStack(alignment: .leading) {
                    Text("Pocket Dimension").font(.largeTitle.bold()).tracking(-0.8)
                    Text("A USB portal for your files").foregroundStyle(.secondary)
                }
                Spacer()
                Button("Refresh devices", action: model.refreshDevices).disabled(model.busy)
            }
            .padding(16)
            .background(LinearGradient(colors: [PortalTheme.blue.opacity(0.12), PortalTheme.cyan.opacity(0.04)], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(PortalTheme.blue.opacity(0.18)))
            HStack {
                Picker("Phone", selection: $model.deviceID) {
                    Text("Choose a device").tag("")
                    ForEach(model.devices) { device in Text(device.displayName).tag(device.id) }
                }.frame(maxWidth: 520)
                Button("Connect", action: model.connect).buttonStyle(.borderedProminent).tint(PortalTheme.blue)
                Menu("Security") { Button("Revoke this computer on phone", action: model.forgetPhone).disabled(model.busy || model.deviceID.isEmpty) }.fixedSize()
            }
            .disabled(model.busy)
            Divider()
            HStack(spacing: 8) {
                Button(action: model.back) { Image(systemName: "chevron.left") }
                    .disabled(model.backHistory.isEmpty).help("Back (⌘[)").keyboardShortcut("[", modifiers: .command)
                Button(action: model.forward) { Image(systemName: "chevron.right") }
                    .disabled(model.forwardHistory.isEmpty).help("Forward (⌘])").keyboardShortcut("]", modifiers: .command)
                Button(action: model.up) { Image(systemName: "arrow.up") }
                    .disabled(model.remote.isEmpty).help("Enclosing folder (⌘↑)").keyboardShortcut(.upArrow, modifiers: .command)
                Text(model.rootName).fontWeight(.semibold).lineLimit(1).truncationMode(.middle).frame(maxWidth: 200, alignment: .leading)
                TextField("Relative folder path", text: $model.pathInput).textFieldStyle(.roundedBorder).onSubmit(model.go)
                Button("Go", action: model.go).disabled(!model.shared)
                Button(action: model.refresh) { Image(systemName: "arrow.clockwise") }
                    .disabled(!model.shared).help("Refresh folder (⌘R)").keyboardShortcut("r", modifiers: .command)
            }
            .disabled(model.busy)
            HStack {
                TextField("Filter this folder", text: $model.search).textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                    .onChange(of: model.search) { _ in model.selection.formIntersection(Set(model.visibleEntries.map(\.id))) }
                Spacer()
                Text("\(model.visibleEntries.count) items" + (model.selection.isEmpty ? "" : " · \(model.selection.count) selected"))
                    .foregroundStyle(.secondary).font(.callout)
            }
            .disabled(model.busy)
            Table(model.visibleEntries, selection: $model.selection, sortOrder: $model.sortOrder) {
                TableColumn("Name", value: \.name) { entry in
                    Label(entry.name, systemImage: entry.folder ? "folder.fill" : "doc")
                        .foregroundStyle(entry.folder ? PortalTheme.blue : .primary)
                }
                TableColumn("Kind", value: \.type) { Text($0.folder ? "Folder" : "File") }.width(80)
                TableColumn("Size", value: \.sortSize) { entry in
                    Text(entry.size.map { ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) } ?? "—")
                }.width(110)
                TableColumn("Modified", value: \.sortModified) { entry in
                    Text(entry.modified.map { Date(timeIntervalSince1970: Double($0) / 1000).formatted(date: .abbreviated, time: .shortened) } ?? "—")
                }.width(150)
            }
            .onDrop(of: [UTType.fileURL], isTargeted: $model.dropTargeted, perform: model.receiveDrop)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(model.dropTargeted ? PortalTheme.cyan : Color.clear, lineWidth: 2))
            .contextMenu(forSelectionType: Entry.ID.self) { ids in
                if ids.count == 1, let entry = model.entries.first(where: { ids.contains($0.id) }), entry.folder {
                    Button("Open folder") { model.enter(entry) }
                }
                if !ids.isEmpty { Button("Download \(ids.count) selected item(s)…") { model.pull(ids: ids) } }
                Button("Select all", action: model.selectAll).keyboardShortcut("a", modifiers: .command)
                Button("Refresh", action: model.refresh)
            } primaryAction: { ids in model.primaryAction(ids) }
            .frame(minHeight: 260)
            .disabled(model.busy)
            HStack {
                Button(model.selection.isEmpty ? (model.rootIsFolder ? "Download current folder…" : "Download shared file…") : "Download \(model.selection.count) selected…") { model.pull() }
                    .disabled(!model.shared)
                Button("Upload files or folders…", action: model.push).disabled(!model.writable || !model.shared)
                if let entry = model.selectedFolder { Button("Open folder") { model.enter(entry) } }
                Spacer()
                Button("Select all", action: model.selectAll).keyboardShortcut("a", modifiers: .command).disabled(model.entries.isEmpty)
                if !model.selection.isEmpty { Button("Clear selection") { model.selection = [] } }
            }
            .disabled(model.busy)
            HStack {
                Picker("If a name exists", selection: $model.conflictPolicy) {
                    ForEach(ConflictPolicy.allCases) { policy in Text(policy.title).tag(policy) }
                }.frame(maxWidth: 240).disabled(model.busy)
                Toggle("Resume", isOn: $model.resumeEnabled).disabled(model.busy).help("Prefix-verified resume requires the latest Android APK")
                Spacer()
                Button("Transfers (\(model.queue.count))") { model.showQueue = true }
                if model.busy && model.batchCount > 0 {
                    Button("Skip item", action: model.skipCurrent).disabled(model.cancelling || model.planning)
                    Button(model.cancelling ? "Cancelling…" : "Cancel queue", action: model.cancelQueue).disabled(model.cancelling)
                } else if model.hasUnfinished {
                    Button("Retry unfinished", action: model.retryUnfinished)
                }
            }
            if model.batchCount > 0 || model.overallTotal != nil {
                VStack(alignment: .leading, spacing: 5) {
                    if let total = model.overallTotal, total > 0 { ProgressView(value: min(Double(model.overallBytes), Double(total)), total: Double(total)) }
                    Text("\(ByteCountFormatter.string(fromByteCount: model.overallBytes, countStyle: .file))" + (model.overallTotal.map { " / " + ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) } ?? " · total size unknown") + " · \(model.remainingCount) remaining · " + model.etaText).font(.caption).foregroundStyle(.secondary)
                }
            }
            if model.batchCount > 1 { Text("Item \(model.batchIndex) of \(model.batchCount)").font(.callout).foregroundStyle(.secondary) }
            if let progress = model.progress {
                VStack(alignment: .leading, spacing: 6) {
                    Text(progress.name).lineLimit(1).truncationMode(.middle)
                    if let total = progress.total, total > 0 {
                        ProgressView(value: min(Double(progress.bytes), Double(total)), total: Double(total))
                    } else { ProgressView().progressViewStyle(.linear) }
                    Text("\(ByteCountFormatter.string(fromByteCount: progress.bytes, countStyle: .file)) transferred").font(.caption).foregroundStyle(.secondary)
                }
            } else if model.busy { ProgressView().controlSize(.small) }
            Text(model.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            Text("Double-click a folder to open it. Use ⌘-click or Shift-click to select multiple items. Existing files are never overwritten.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(24).frame(minWidth: 780, minHeight: 660)
        .tint(PortalTheme.blue)
        .background(PortalTheme.blue.opacity(0.025))
        .sheet(isPresented: $model.showPairing) { PairingView(model: model) }
        .sheet(isPresented: $model.showQueue) { TransferQueueView(model: model) }
        .onAppear(perform: model.refreshDevices)
    }
}

struct PairingView: View {
    @ObservedObject var model: Model
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { PortalMark(); Text("Compare with your phone").font(.title2.bold()) }
            Text("Pairing encrypts the connection and remembers this computer. It does not share any files automatically.")
            if let info = model.pairingInfo {
                Text("Computer: " + info.host_name).fontWeight(.semibold)
                Text("Computer fingerprint").font(.caption).foregroundStyle(.secondary)
                Text(PairingInfo.formatted(info.host_fingerprint)).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                Text("Phone fingerprint").font(.caption).foregroundStyle(.secondary)
                Text(PairingInfo.formatted(info.phone_fingerprint)).font(.system(.body, design: .monospaced)).textSelection(.enabled)
            }
            Text("Request pairing, then compare BOTH fingerprints with the approval screen on your unlocked phone. Trust is approved on the phone only.").font(.callout)
            Text(model.pairingStatus).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            HStack {
                Button("Cancel", action: model.cancelPairing)
                Spacer()
                if model.busy { ProgressView().controlSize(.small); Text("Waiting for phone approval…") }
                Button("Request pairing on phone", action: model.pairComputer).buttonStyle(.borderedProminent).disabled(model.busy || model.pairingInfo == nil)
            }
        }.padding(24).frame(width: 620).tint(PortalTheme.blue).interactiveDismissDisabled(model.busy)
    }
}

struct TransferQueueView: View {
    @ObservedObject var model: Model
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("Transfers").font(.title2.bold()); Spacer(); Button("Done") { dismiss() } }
            List(model.queue) { item in
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.job.name).fontWeight(.medium)
                        if !item.detail.isEmpty { Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(3) }
                    }
                    Spacer()
                    Text(item.status.rawValue).foregroundStyle(item.status == .failed ? .red : .secondary)
                    if item.location != nil && item.status == .completed { Button("Reveal") { model.reveal(item) } }
                }.padding(.vertical, 4)
            }.frame(minHeight: 240)
            if let progress = model.progress {
                Text("\(progress.name) · \(ByteCountFormatter.string(fromByteCount: progress.bytes, countStyle: .file))")
                    .lineLimit(1).truncationMode(.middle).font(.caption)
            }
            HStack {
                if model.busy && model.batchCount > 0 {
                    Button("Skip item", action: model.skipCurrent).disabled(model.cancelling || model.planning)
                    Button(model.cancelling ? "Cancelling…" : "Cancel queue", action: model.cancelQueue).disabled(model.cancelling)
                } else {
                    Button("Retry unfinished", action: model.retryUnfinished).disabled(!model.hasUnfinished)
                    Button("Skip failed and continue", action: model.skipFailedAndContinue).disabled(!model.hasFailed)
                    Button("Clear completed", action: model.clearFinished)
                }
                Spacer()
            }
            Text("Completed items are kept. Retry restarts unfinished files from the beginning; enable Resume to retain and verify partial data with the latest Android APK.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(minWidth: 660, minHeight: 400).tint(PortalTheme.blue)
    }
}

#if !GUI_TESTING
@main struct PocketDimensionApp: App {
    var body: some Scene {
        WindowGroup("Pocket Dimension") { ContentView() }
            .defaultSize(width: 900, height: 700)
    }
}

#endif
