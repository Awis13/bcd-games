// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
import Foundation
import CoreGraphics
import CoreText
import ImageIO
import UniformTypeIdentifiers

// shrink <source> <outdir> <shrink percents...>: the artwork scaled to (100 - p)% of the icon, its drawing
// (pixels brighter than the background) centred, the rest filled with the artwork's own background, the cut
// edge of the scaled artwork feathered; prints how close the visible drawing comes to iOS's mask.
let a = CommandLine.arguments
let srcURL = URL(fileURLWithPath: a[1]), out = URL(fileURLWithPath: a[2])
let shrinks = a.dropFirst(3).map { Double($0)! }
let S = 1024.0
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let image = CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithURL(srcURL as CFURL, nil)!, 0, nil)!
let w = image.width, h = image.height
var px = [UInt8](repeating: 0, count: w * h * 4)
let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
func pixel(_ x: Int, _ y: Int) -> (Int, Int, Int) { let i = (y * w + x) * 4; return (Int(px[i]), Int(px[i + 1]), Int(px[i + 2])) }
var rs: [Int] = [], gs: [Int] = [], bs: [Int] = []
for (ox, oy) in [(0, 0), (w - 24, 0), (0, h - 24), (w - 24, h - 24)] {
    for y in oy..<oy + 24 { for x in ox..<ox + 24 { let p = pixel(x, y); rs.append(p.0); gs.append(p.1); bs.append(p.2) } }
}
func median(_ v: [Int]) -> Int { v.sorted()[v.count / 2] }
let bg = (median(rs), median(gs), median(bs))
// The drawing's box (bright pixels; the faint streaks are left out of the centring) and the visible pixels.
// Note: rows in the buffer run bottom-up from CGContext's point of view; both boxes use buffer rows, and the
// drawing below uses the same orientation, so the arithmetic is consistent.
var box = (w, h, 0, 0)
var visible: [(Double, Double)] = []
for y in stride(from: 0, to: h, by: 2) { for x in stride(from: 0, to: w, by: 2) {
    let p = pixel(x, y), d = max(abs(p.0 - bg.0), abs(p.1 - bg.1), abs(p.2 - bg.2))
    if d > 70 { box = (min(box.0, x), min(box.1, y), max(box.2, x), max(box.3, y)) }
    if d > 40 { visible.append((Double(x), Double(y))) }
} }
let bcx = Double(box.0 + box.2) / 2, bcy = Double(box.1 + box.3) / 2
print("bg=\(bg) drawing box=\(box) centre=(\(bcx),\(bcy))")
// Feathered copy of the artwork: the outer 6% fades to transparent, so the cut edge melts into the fill.
var src = px
// Narrow across (the controller starts 5% from the left edge), wide up and down (the drawing leaves room there);
// a smoothstep so the fade has no visible start.
let rampX = Double(w) * 0.045, rampY = Double(h) * 0.16
func smooth(_ t: Double) -> Double { let c = min(max(t, 0), 1); return c * c * (3 - 2 * c) }
for y in 0..<h { for x in 0..<w {
    let fx = smooth(Double(min(x, w - 1 - x)) / rampX), fy = smooth(Double(min(y, h - 1 - y)) / rampY)
    let f = fx * fy
    if f < 1 { let i = (y * w + x) * 4; for c in 0..<4 { src[i + c] = UInt8(Double(src[i + c]) * f) } }
} }
let art = CGContext(data: &src, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!.makeImage()!
let R = 0.2237 * S
func inside(_ x: Double, _ y: Double) -> Double {
    let cx = min(max(x, R), S - R), cy = min(max(y, R), S - R), dx = x - cx, dy = y - cy
    if dx == 0 && dy == 0 { return min(x, y, S - x, S - y) }
    return R - (dx * dx + dy * dy).squareRoot()
}
func save(_ img: CGImage, _ name: String) {
    let d = CGImageDestinationCreateWithURL(out.appendingPathComponent(name) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(d, img, nil); CGImageDestinationFinalize(d)
}
var made: [(String, CGImage)] = []
for p in shrinks {
    let k = S / Double(w) * (100 - p) / 100          // output px per source px
    // Place the drawing's centre on the canvas centre.
    let ox = S / 2 - bcx * k, oy = S / 2 - bcy * k
    var closest = 1e9
    for (x, y) in visible { closest = min(closest, inside(ox + x * k, oy + y * k)) }
    let c = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    c.setFillColor(CGColor(srgbRed: CGFloat(bg.0) / 255, green: CGFloat(bg.1) / 255, blue: CGFloat(bg.2) / 255, alpha: 1))
    c.fill(CGRect(x: 0, y: 0, width: S, height: S))
    c.interpolationQuality = .high
    c.draw(art, in: CGRect(x: ox, y: oy, width: Double(w) * k, height: Double(h) * k))
    let icon = c.makeImage()!
    save(icon, "shrink-\(Int(p)).png")
    made.append(("-\(Int(p))%", icon))
    print("shrink \(Int(p))%: scale \(String(format: "%.3f", k)); closest visible pixel to the mask edge \(Int(closest)) px (negative = cut)")
}
// Sheet: each variant big and at home-screen sizes, under iOS's shape.
let colW = 400.0, gap = 90.0, height = 1000.0, width = gap + Double(made.count) * colW
let sheet = CGContext(data: nil, width: Int(width), height: Int(height), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let wall = CGGradient(colorsSpace: cs, colors: [CGColor(red: 0.10, green: 0.09, blue: 0.18, alpha: 1),
                                               CGColor(red: 0.42, green: 0.45, blue: 0.62, alpha: 1)] as CFArray, locations: [0, 1])!
sheet.drawLinearGradient(wall, start: CGPoint(x: 0, y: height), end: CGPoint(x: 0, y: 0), options: [])
func draw(_ img: CGImage, _ x: Double, _ y: Double, _ s: Double) {
    let r = CGRect(x: x, y: y, width: s, height: s)
    sheet.saveGState(); sheet.addPath(CGPath(roundedRect: r, cornerWidth: s * 0.2237, cornerHeight: s * 0.2237, transform: nil))
    sheet.clip(); sheet.interpolationQuality = .high; sheet.draw(img, in: r); sheet.restoreGState()
}
for (i, (name, img)) in made.enumerated() {
    let x0 = gap + Double(i) * colW
    let attrs = [kCTFontAttributeName: CTFontCreateWithName("Helvetica-Bold" as CFString, 34, nil),
                 kCTForegroundColorAttributeName: CGColor(red: 1, green: 1, blue: 1, alpha: 1)] as CFDictionary
    sheet.textPosition = CGPoint(x: x0, y: height - 60)
    CTLineDraw(CTLineCreateWithAttributedString(CFAttributedStringCreate(nil, name as CFString, attrs)!), sheet)
    draw(img, x0, height - 70 - 380, 360)
    draw(img, x0 + 90, height - 70 - 380 - 60 - 180, 180)
    draw(img, x0 + 120, height - 70 - 380 - 60 - 180 - 40 - 120, 120)
}
save(sheet.makeImage()!, "shrink-sheet.png")
