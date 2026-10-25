import Foundation
import CryptoKit

@main struct PhoneChecks {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("PocketDimension-GUI-USB-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        func require(_ condition: Bool, _ message: String) throws { if !condition { throw NSError(domain: "PhoneChecks", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) } }
        let executable = URL(fileURLWithPath: CommandLine.arguments[1])
        let history = root.appendingPathComponent("history.json")
        let model = Model(executable: executable, historyURL: history)
        func idle() async throws {
            for _ in 0..<12000 { if !model.busy { return }; try await Task.sleep(nanoseconds: 10_000_000) }
            throw NSError(domain: "PhoneChecks", code: 2, userInfo: [NSLocalizedDescriptionKey: "Timed out: " + model.message])
        }
        func transferring() async throws {
            for _ in 0..<6000 {
                if let progress=model.progress, progress.bytes >= 16379 && model.busy { return }
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            throw NSError(domain:"PhoneChecks",code:3,userInfo:[NSLocalizedDescriptionKey:"No live USB progress: " + model.message])
        }
        model.connect(); try await idle()
        try require(model.shared && model.rootName.hasPrefix("PocketDimension-USB-check-"), "Refusing to test outside generated fixture")
        func job(_ name: String, conflict: String = "error") -> TransferJob {
            TransferJob(name:name,arguments:["pull",root.appendingPathComponent(name).path,"--remote","large.bin","--json","--json-progress","--conflict",conflict],localDestination:root.appendingPathComponent(name),expectedBytes:12582912)
        }
        model.runTransfers([job("cancel.bin"),job("after-cancel.bin")]) { }
        try await transferring(); model.cancelQueue(); try await idle()
        try require(model.queue[0].status == .cancelled && model.queue[1].status == .waiting && !FileManager.default.fileExists(atPath:root.appendingPathComponent("cancel.bin").path),"Cancel must preserve waiting items and absent final output")
        model.retryUnfinished(); try await idle()
        try require(model.queue.allSatisfy { $0.status == .completed }, "Physical retry must complete both unfinished jobs: " + model.message)
        print("PASS physical GUI queue cancel and resume/retry",terminator:"\n")
        model.runTransfers([job("skip.bin"),job("after-skip.bin")]) { }
        try await transferring(); model.skipCurrent(); try await idle()
        try require(model.queue[2].status == .skipped && model.queue[3].status == .completed && !FileManager.default.fileExists(atPath:root.appendingPathComponent("skip.bin").path),"Physical Skip must wait for safe cleanup and continue")
        print("PASS physical GUI Skip and queue continuation")
        try Data("keep".utf8).write(to:root.appendingPathComponent("collision.bin"))
        model.runTransfers([job("collision.bin"),job("after-failure.bin")]) { }
        try await idle()
        try require(model.queue[4].status == .failed && model.queue[5].status == .waiting,"Stop on physical conflict")
        try require(try String(contentsOf:root.appendingPathComponent("collision.bin"),encoding:.utf8)=="keep","Original destination intact")
        try FileManager.default.removeItem(at:root.appendingPathComponent("collision.bin"))
        model.retryUnfinished();try await idle()
        try require(model.queue[4].status == .completed && model.queue[5].status == .completed,"Retry failed and waiting jobs only")
        print("PASS physical GUI conflict refusal and retry")
        let first=try Data(contentsOf:root.appendingPathComponent("cancel.bin"))
        for name in ["after-cancel.bin","after-skip.bin","collision.bin","after-failure.bin"] {
            try require(SHA256.hash(data:Data(contentsOf:root.appendingPathComponent(name))) == SHA256.hash(data:first),"GUI download checksum differs")
        }
        try require(model.overallBytes == model.overallTotal && model.remainingCount == 0,"Physical aggregate progress completes")
        model.persistHistory()
        let restored=Model(executable:executable,historyURL:history)
        try require(restored.queue.count==6 && !restored.busy,"Physical queue history restored without transfer")
        var refused=false
        model.run(["pull",root.appendingPathComponent("wrong-phone").path,"--remote","large.bin","--device-id","wrong-phone","--json"],failed:{_,_ in refused=true}){_ in}
        try await idle();try require(refused && !FileManager.default.fileExists(atPath:root.appendingPathComponent("wrong-phone").path),"Wrong phone identity must refuse before data")
        refused=false
        model.run(["pull",root.appendingPathComponent("wrong-scope").path,"--remote","large.bin","--scope-id","wrong-scope","--json"],failed:{_,_ in refused=true}){_ in}
        try await idle();try require(refused && !FileManager.default.fileExists(atPath:root.appendingPathComponent("wrong-scope").path),"Wrong sharing scope must refuse before data")
        print("PASS physical GUI bytes, checksums, persistent history, and phone/scope refusal")
        try "Physical GUI USB checks passed: cancel, skip, retry, conflicts, aggregate progress, history, identity refusal, SHA-256 readback.\n".write(toFile:"build/phone-gui-acceptance.txt",atomically:true,encoding:.utf8)
    }
}
