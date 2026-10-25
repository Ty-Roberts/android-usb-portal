import AppKit
let root = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
for size in [16, 32, 64, 128, 256, 512, 1024] {
    let image = NSImage(size: NSSize(width: 1024, height: 1024))
    image.lockFocus()
    let background = NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210)
    NSGradient(starting: NSColor(calibratedRed: 0.08, green: 0.10, blue: 0.24, alpha: 1), ending: NSColor(calibratedRed: 0.02, green: 0.035, blue: 0.13, alpha: 1))!.draw(in: background, angle: 65)
    let folder = NSBezierPath()
    folder.move(to: NSPoint(x: 208,y: 300)); folder.line(to: NSPoint(x: 208,y: 664))
    folder.curve(to: NSPoint(x: 256,y: 712), controlPoint1: NSPoint(x: 208,y: 696), controlPoint2: NSPoint(x: 224,y: 712))
    folder.line(to: NSPoint(x: 424,y: 712)); folder.line(to: NSPoint(x: 484,y: 652)); folder.line(to: NSPoint(x: 768,y: 652))
    folder.curve(to: NSPoint(x: 816,y: 604), controlPoint1: NSPoint(x: 800,y: 652),controlPoint2: NSPoint(x: 816,y: 636))
    folder.line(to: NSPoint(x: 816,y: 300)); folder.close()
    NSColor(calibratedRed: 0.12,green: 0.26,blue: 0.62,alpha: 1).setFill(); folder.fill()
    NSColor(calibratedRed: 0.18,green: 0.40,blue: 0.94,alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 184,y: 272,width: 656,height: 330),xRadius: 54,yRadius: 54).fill()
    let portal = NSBezierPath(ovalIn: NSRect(x: 374,y: 302,width: 276,height: 364))
    NSColor(calibratedRed: 0.025,green: 0.07,blue: 0.20,alpha: 1).setFill(); portal.fill()
    NSColor(calibratedRed: 0.40,green: 0.85,blue: 1,alpha: 1).setStroke(); portal.lineWidth = 27; portal.stroke()
    let inner = NSBezierPath(ovalIn: NSRect(x: 428,y: 368,width: 168,height: 232))
    NSColor(calibratedRed: 0.25,green: 0.55,blue: 1,alpha: 1).setStroke();inner.lineWidth = 13; inner.stroke()
    NSColor.white.setFill(); NSBezierPath(roundedRect: NSRect(x: 704,y: 700,width: 44,height: 44),xRadius: 10,yRadius: 10).fill()
    image.unlockFocus()
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil,pixelsWide: size,pixelsHigh: size,bitsPerSample: 8,samplesPerPixel: 4,hasAlpha: true,isPlanar: false,colorSpaceName: .deviceRGB,bytesPerRow: 0,bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0,y: 0,width: size,height: size));NSGraphicsContext.restoreGraphicsState()
    let data = rep.representation(using: .png,properties: [:])!
    if [16, 32, 128, 256, 512].contains(size) { try data.write(to: root.appendingPathComponent("icon_\(size)x\(size).png")) }
    if [32, 64, 256, 512, 1024].contains(size) { try data.write(to: root.appendingPathComponent("icon_\(size/2)x\(size/2)@2x.png")) }
}
