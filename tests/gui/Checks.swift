import Foundation
import AppKit
import UniformTypeIdentifiers

@main struct GUIChecks {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pocket-gui-checks-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        func require(_ condition: Bool, _ message: String) throws {
            if !condition { throw NSError(domain: "GUIChecks", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }
        }
        let legacyDevice = try JSONDecoder().decode(Device.self, from: Data(#"{"vid":6353,"pid":11521,"bus":2,"address":14,"name":"Android accessory"}"#.utf8))
        try require(legacyDevice.displayName.contains("USB 2:14"), "Legacy device location fallback")
        let phoneA = Device(vid: 6353, pid: 11521, bus: 2, address: 14, name: "Samsung SM-S918U1", device_id: "phone-111111111111")
        let phoneB = Device(vid: 6353, pid: 11521, bus: 2, address: 15, name: "Samsung SM-S918U1", device_id: "phone-222222222222")
        try require(phoneA.displayName.contains("Phone ID 111111111111") && phoneA.displayName != phoneB.displayName, "Identical phone models have distinct picker labels")
        let usbPhone = Device(vid: 1256, pid: 26720, bus: 2, address: 16, name: "Galaxy", serial: "R5C-example")
        try require(usbPhone.displayName.contains("Serial R5C-example"), "USB serial displayed")
        let executable = root.appendingPathComponent("fake-cli")
        let script = #"""
#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '%s\n' "$*" >> "$SCRIPT_DIR/log"
command=$1
shift
case "$command" in
needs-pair) printf '%s\n' '{"event":"pairing_required","phone_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","host_fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","host_name":"Fixture","reason":"Pair this computer","device":{"vid":6353,"pid":11521,"bus":1,"address":4,"name":"Fixture phone"}}' >&2; exit 3 ;;
ls)
    path=''
    while [ "$#" -gt 0 ]; do
        if [ "$1" = '--remote' ]; then shift; path=$1; fi
        shift
    done
    case "$path" in
    '') printf '[{"name":"Docs","type":"directory","size":null,"path":"Docs"},{"name":"a.txt","type":"file","size":3,"path":"a.txt"},{"name":"b.txt","type":"file","size":4,"path":"b.txt"}]\n' ;;
    Docs) printf '[{"name":"nested.txt","type":"file","size":5,"path":"Docs/nested.txt"}]\n' ;;
    *) printf 'Pocket Dimension: No such folder\n' >&2; exit 1 ;;
    esac ;;
slow) trap 'printf "Cancelled safely\n" >&2; exit 130' INT
      touch "$SCRIPT_DIR/ready"
      while :; do sleep 0.05; done ;;
flaky) if [ ! -f "$SCRIPT_DIR/tried" ]; then touch "$SCRIPT_DIR/tried"; printf 'Temporary failure\n' >&2; exit 1; fi
       printf '{"bytes":4}\n' ;;
fail) printf 'Pocket Dimension: destination exists\n' >&2; exit 1 ;;
*) printf '{"event":"progress","name":"test.bin","bytes":4,"total":4}\n' >&2
   printf '{"bytes":4}\n' ;;
esac
"""#
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        let model = Model(executable: executable)
        model.shared = true; model.rootIsFolder = true; model.rootName = "Test phone"
        func idle() async throws {
            for _ in 0..<500 {
                if !model.busy { return }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            throw NSError(domain: "GUIChecks", code: 2, userInfo: [NSLocalizedDescriptionKey: "Timed out"])
        }
        model.run(["needs-pair"]) { _ in }
        try await idle()
        try require(model.showPairing && model.pairingInfo?.valid == true && model.devices.first(where: { $0.id == model.deviceID })?.address == 4, "Pairing prompt preserves full fingerprints and switched USB address")
        model.cancelPairing()
        try require(!model.showPairing && model.pairingInfo == nil, "Pairing cancellation clears prompt")
        model.refresh(); try await idle()
        try require(model.entries.count == 3 && model.remote == "", "Initial listing")
        model.selection = ["a.txt", "b.txt"]
        try require(model.selectedEntries.count == 2, "Multiple selection")
        model.primaryAction(["a.txt", "b.txt"])
        try require(!model.busy, "Multi-item primary action should not navigate")
        model.primaryAction(["Docs"]); try await idle()
        try require(model.remote == "Docs" && model.backHistory == [""] && model.selection.isEmpty, "Folder primary action")
        model.back(); try await idle()
        try require(model.remote == "" && model.forwardHistory == ["Docs"], "Back navigation")
        model.forward(); try await idle()
        try require(model.remote == "Docs", "Forward navigation")
        model.up(); try await idle()
        try require(model.remote.isEmpty, "Up navigation")
        model.pathInput = "missing"; model.go(); try await idle()
        try require(model.remote.isEmpty && model.entries.count == 3, "Failed navigation must preserve current listing")
        model.pathInput = "unsubmitted-edit"; model.refresh(); try await idle()
        try require(model.remote.isEmpty && model.pathInput.isEmpty, "Refresh must use committed folder")
        model.search = "a.txt"; model.selectAll()
        try require(model.selection == ["a.txt"], "Select all must respect filter")
        model.search = ""
        let log = root.appendingPathComponent("log")
        try "".write(to: log, atomically: true, encoding: .utf8)
        var completed = false
        model.runTransfers(["first", "second", "third"].map { TransferJob(name: $0, arguments: [$0]) }) { completed = true }
        try await idle()
        try require(completed && model.batchCount == 0 && model.batchIndex == 0, "Batch completion state")
        try require(try String(contentsOf: log, encoding: .utf8) == "first\nsecond\nthird\n", "Transfers must run sequentially")
        try "".write(to: log, atomically: true, encoding: .utf8)
        completed = false
        model.runTransfers(["first", "fail", "never"].map { TransferJob(name: $0, arguments: [$0]) }) { completed = true }
        try await idle()
        try require(!completed && model.message.contains("Stopped after 1 of 3"), "Batch must report partial completion")
        try require(try String(contentsOf: log, encoding: .utf8) == "first\nfail\n", "Batch must stop on first error")
        model.skipFailedAndContinue(); try await idle()
        try require(try String(contentsOf: log, encoding: .utf8) == "first\nfail\nnever\n", "Skip failed must continue only waiting jobs")
        try "".write(to: log, atomically: true, encoding: .utf8)
        model.runTransfers(["flaky", "after-retry"].map { TransferJob(name: $0, arguments: [$0]) }) { }
        try await idle()
        try require(model.hasUnfinished, "Failed batch must remain retryable")
        model.retryUnfinished(); try await idle()
        try require(try String(contentsOf: log, encoding: .utf8) == "flaky\nflaky\nafter-retry\n", "Retry must not repeat earlier successful items")
        try require(!model.hasUnfinished, "Successful retry must clear unfinished state")
        try "".write(to: log, atomically: true, encoding: .utf8)
        model.runTransfers(["slow", "after-cancel"].map { TransferJob(name: $0, arguments: [$0]) }) { }
        for _ in 0..<300 {
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("ready").path) { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        model.cancelQueue(); try await idle()
        try require(try String(contentsOf: log, encoding: .utf8) == "slow\n", "Cancel must stop waiting jobs")
        try require(model.queue.contains { $0.status == .cancelled } && model.hasUnfinished, "Cancelled item remains retryable")
        model.skipFailedAndContinue(); try await idle()
        try require(try String(contentsOf: log, encoding: .utf8) == "slow\nafter-cancel\n", "Continue after cancellation")
        try FileManager.default.removeItem(at: root.appendingPathComponent("ready"))
        try "".write(to: log, atomically: true, encoding: .utf8)
        completed = false
        model.runTransfers(["slow", "after-skip"].map { TransferJob(name: $0, arguments: [$0]) }) { completed = true }
        for _ in 0..<300 {
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("ready").path) { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        model.skipCurrent(); try await idle()
        try require(completed && (try String(contentsOf: log, encoding: .utf8)) == "slow\nafter-skip\n", "Skip current must continue queue after cleanup")
        let entries = [Entry(name: "a.txt", type: "file", size: 3, path: "a.txt"), Entry(name: "Docs", type: "directory", size: nil, path: "Docs")]
        let targets = try downloadDestinations(entries: entries, parent: root)
        try require(targets.map(\.lastPathComponent) == ["a.txt", "Docs"], "Batch destinations")
        for bad in ["../escape", "/absolute", ".", "..", "back\\slash", "line\nbreak"] {
            do { _ = try safeLocalName(bad); throw NSError(domain: "GUIChecks", code: 3) }
            catch BrowserError.unsafeName { }
        }
        let existing = root.appendingPathComponent("a.txt")
        try "original".write(to: existing, atomically: true, encoding: .utf8)
        do { _ = try downloadDestinations(entries: entries, parent: root); throw NSError(domain: "GUIChecks", code: 4) }
        catch BrowserError.existingDestination { }
        try require(try String(contentsOf: existing, encoding: .utf8) == "original", "No-overwrite preflight")
        let collision = [Entry(name: "Report", type: "file", size: 0, path: "Report"), Entry(name: "report", type: "file", size: 0, path: "report")]
        do { _ = try downloadDestinations(entries: collision, parent: root); throw NSError(domain: "GUIChecks", code: 5) }
        catch BrowserError.duplicateName { }
        let broken = root.appendingPathComponent("broken")
        try FileManager.default.createSymbolicLink(at: broken, withDestinationURL: root.appendingPathComponent("missing"))
        do { _ = try downloadDestinations(entries: [Entry(name: "broken", type: "file", size: 0, path: "broken")], parent: root); throw NSError(domain: "GUIChecks", code: 6) }
        catch BrowserError.existingDestination { }
        let dropped = Model(executable: executable)
        dropped.shared = true; dropped.writable = true
        let firstDrop = root.appendingPathComponent("drop-first.txt"), secondDrop = root.appendingPathComponent("drop-second.txt")
        try "first".write(to: firstDrop, atomically: true, encoding: .utf8)
        try "second".write(to: secondDrop, atomically: true, encoding: .utf8)
        let providers = [firstDrop, secondDrop, firstDrop].map { NSItemProvider(item: $0 as NSURL, typeIdentifier: UTType.fileURL.identifier) }
        try require(dropped.receiveDrop(providers), "Accept local file drops")
        for _ in 0..<500 {
            if dropped.queue.count == 2 && !dropped.busy { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try require(dropped.queue.map { $0.job.name } == ["drop-first.txt", "drop-second.txt"] && dropped.queue.allSatisfy { $0.status == .completed }, "Drops preserve order, remove duplicate URLs, and use upload queue")
        try require(dropped.overallBytes == 8 && dropped.overallTotal == 8 && dropped.remainingCount == 0, "Overall queue bytes finish consistently")
        dropped.writable = false
        try require(!dropped.receiveDrop(providers), "Reject drops into read-only scopes")
        let history = root.appendingPathComponent("history/items.json")
        let saved = Model(executable: executable, historyURL: history)
        var interrupted = QueuedTransfer(job: TransferJob(name: "interrupted", arguments: ["pull", "/tmp/example"]))
        interrupted.status = .running; interrupted.transferredBytes = 8192
        saved.queue = [interrupted]; saved.persistHistory()
        let restored = Model(executable: executable, historyURL: history)
        try require(restored.queue.count == 1 && restored.queue[0].status == .waiting && restored.queue[0].transferredBytes == 8192 && !restored.busy, "Persistent history restores interrupted work without starting it")
        model.entries = [Entry(name: "large", type: "file", size: 50, path: "large", modified: 1000), Entry(name: "folder", type: "directory", size: nil, path: "folder"), Entry(name: "small", type: "file", size: 1, path: "small", modified: 2000)]
        model.sortOrder = [KeyPathComparator(\Entry.sortSize, order: .reverse)]
        try require(model.visibleEntries.map(\.name) == ["folder", "large", "small"], "Size sort preserves folders first")
        model.sortOrder = [KeyPathComparator(\Entry.sortModified, order: .reverse)]
        try require(model.visibleEntries.map(\.name) == ["folder", "small", "large"], "Modified date sort")
        print("GUI checks passed: navigation, destination safety, queue history, cancellation, skip, and retry")
    }
}
