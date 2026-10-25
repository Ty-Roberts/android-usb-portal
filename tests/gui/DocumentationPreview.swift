import SwiftUI
import AppKit

// Documentation only: the real desktop view with synthetic data and an inert CLI.
// No USB, Keychain, personal files, or production transfer history is accessed.
@main struct DocumentationPreview: App {
    @StateObject private var model: Model
    init() {
        let helper = URL(fileURLWithPath: CommandLine.arguments[1])
        let model = Model(executable: helper)
        let phone = Device(vid:6353,pid:11521,bus:1,address:1,name:"Android phone")
        model.devices = [phone]; model.deviceID = phone.id
        model.rootName = "Shared files"; model.shared = true; model.writable = true; model.rootIsFolder = true
        model.entries = [
            Entry(name:"Documents",type:"directory",size:nil,path:"Documents",modified:1792886400000),
            Entry(name:"Photos",type:"directory",size:nil,path:"Photos",modified:1792886400000),
            Entry(name:"Projects",type:"directory",size:nil,path:"Projects",modified:1792886400000),
            Entry(name:"notes.md",type:"file",size:2048,path:"notes.md",modified:1792886400000),
            Entry(name:"welcome.txt",type:"file",size:1024,path:"welcome.txt",modified:1792886400000)
        ]
        model.selection = ["Documents","Photos"]
        model.message = "Connected · browse, select, and move files through your USB portal."
        _model = StateObject(wrappedValue:model)
    }
    var body: some Scene {
        WindowGroup("Pocket Dimension — Documentation preview") { ContentView(model:model).preferredColorScheme(.dark) }
            .defaultSize(width:1000,height:720)
    }
}
