import AppKit
import ApplicationServices
import Foundation

let args = CommandLine.arguments
guard args.count >= 3, let pid = Int32(args[2]), let running = NSRunningApplication(processIdentifier: pid), running.bundleIdentifier == "dev.horizyn.app" else {
    print("Expected the running Horizyn application PID")
    exit(2)
}
let app = AXUIElementCreateApplication(pid)
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func children(_ element: AXUIElement) -> [AXUIElement] { attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func string(_ element: AXUIElement, _ name: String) -> String { (attr(element, name) as? String) ?? "" }
var visited = 0
func dump(_ element: AXUIElement, _ depth: Int = 0) -> [String: Any] {
    visited += 1
    var node: [String: Any] = [:]
    for key in [kAXRoleAttribute, kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute, kAXMenuItemCmdCharAttribute] {
        let value = string(element, key)
        if !value.isEmpty { node[key] = value }
    }
    if let enabled = attr(element, kAXEnabledAttribute) { node["enabled"] = String(describing: enabled) }
    if depth < 9 && visited < 1600 { node["children"] = children(element).map { dump($0, depth + 1) } }
    return node
}
func findMenu(_ element: AXUIElement, _ title: String, _ depth: Int = 0) -> [AXUIElement] {
    if depth > 10 { return [] }
    var result: [AXUIElement] = []
    if string(element, kAXRoleAttribute) == kAXMenuItemRole && string(element, kAXTitleAttribute) == title { result.append(element) }
    for child in children(element) { result += findMenu(child, title, depth + 1) }
    return result
}
func emit(_ value: Any) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}
switch args[1] {
case "dump":
    emit(["trusted": AXIsProcessTrusted(), "tree": dump(app)])
case "menu":
    guard args.count >= 4 else { exit(2) }
    let matches = findMenu(app, args[3])
    guard matches.count == 1 else { emit(["error": "Menu title did not uniquely identify an item", "count": matches.count]); exit(3) }
    running.activate(options: [.activateIgnoringOtherApps])
    let status = AXUIElementPerformAction(matches[0], kAXPressAction as CFString)
    emit(["title": args[3], "status": status.rawValue])
    if status != .success { exit(4) }
case "key":
    guard args.count >= 5, let key = UInt16(args[3]), let flags = UInt64(args[4]) else { exit(2) }
    running.activate(options: [.activateIgnoringOtherApps])
    usleep(200_000)
    for down in [true, false] {
        let event = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: down)!
        event.flags = CGEventFlags(rawValue: flags)
        event.postToPid(pid)
    }
    emit(["key": key, "flags": flags])
case "type":
    guard args.count >= 4 else { exit(2) }
    running.activate(options: [.activateIgnoringOtherApps])
    usleep(200_000)
    let units = Array(args[3].utf16)
    for down in [true, false] {
        let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down)!
        units.withUnsafeBufferPointer { event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: $0.baseAddress!) }
        event.postToPid(pid)
    }
    emit(["typedCharacters": units.count])
default:
    exit(2)
}
