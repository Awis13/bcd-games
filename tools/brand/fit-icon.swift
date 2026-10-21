// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// Fit a square artwork with a neon frame inside iOS's icon mask (continuous rounded square, radius 22.37%),
// keeping every visible pixel of the frame and its glow at least `margin` px inside the mask.
let args = CommandLine.arguments
let srcURL = URL(fileURLWithPath: args[1]), outURL = URL(fileURLWithPath: args[2]), previewURL = URL(fileURLWithPath: args[3])
let S = 1024.0, margin = Double(args.count > 4 ? args[4] : "22")!, threshold = Int(args.count > 5 ? args[5] : "26")!

guard let source = CGImageSourceCreateWithURL(srcURL as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fatalError("read") }
let w = image.width, h = image.height
var px = [UInt8](repeating: 0, count: w * h * 4)
let rgb = CGColorSpaceCreateDeviceRGB()
let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: rgb,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))

// Background: the median of the four corner patches.
func pixel(_ x: Int, _ y: Int) -> (Int, Int, Int) { let i = (y * w + x) * 4; return (Int(px[i]), Int(px[i + 1]), Int(px[i + 2])) }
var rs: [Int] = [], gs: [Int] = [], bs: [Int] = []
for (ox, oy) in [(0, 0), (w - 24, 0), (0, h - 24), (w - 24, h - 24)] {
    for y in oy..<oy + 24 { for x in ox..<ox + 24 { let p = pixel(x, y); rs.append(p.0); gs.append(p.1); bs.append(p.2) } }
}
func median(_ a: [Int]) -> Int { a.sorted()[a.count / 2] }
let bg = (median(rs), median(gs), median(bs))
// Edge uniformity: how far the outer ring strays from the background.
var edgeMax = 0
for i in 0..<w { for (x, y) in [(i, 0), (i, h - 1), (0, i % h), (w - 1, i % h)] { let p = pixel(x, y); edgeMax = max(edgeMax, abs(p.0 - bg.0), abs(p.1 - bg.1), abs(p.2 - bg.2)) } }

// Content: pixels that differ from the background by more than the threshold.
var content: [(Double, Double)] = []
var minX = w, minY = h, maxX = 0, maxY = 0
for y in stride(from: 0, to: h, by: 2) { for x in stride(from: 0, to: w, by: 2) {
    let p = pixel(x, y)
    if max(abs(p.0 - bg.0), abs(p.1 - bg.1), abs(p.2 - bg.2)) > threshold {
        content.append((Double(x), Double(y))); minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
    }
} }

// Signed distance inside a rounded square [0,S] with radius R (positive = inside).
let R = 0.2237 * S
func inside(_ x: Double, _ y: Double) -> Double {
    let cx = min(max(x, R), S - R), cy = min(max(y, R), S - R)
    let dx = x - cx, dy = y - cy
    if dx == 0 && dy == 0 { return min(x, y, S - x, S - y) }   // in the straight band
    return R - (dx * dx + dy * dy).squareRoot()
}
let sc = Double(w) / 2
func fits(_ k: Double) -> Bool {
    for (x, y) in content {
        let X = S / 2 + (x - sc) * k, Y = S / 2 + (y - sc) * k
        if inside(X, Y) < margin { return false }
    }
    return true
}
var lo = 0.3, hi = S / Double(w)
for _ in 0..<30 { let mid = (lo + hi) / 2; if fits(mid) { lo = mid } else { hi = mid } }
let k = lo

// Compose: background fill, then the artwork scaled by k with a soft edge (outer 5% of the artwork fades out).
var src = px
let ramp = Double(w) * 0.05
for y in 0..<h { for x in 0..<w {
    let d = Double(min(x, y, w - 1 - x, h - 1 - y))
    if d < ramp {
        let a = d / ramp, i = (y * w + x) * 4
        for c in 0..<4 { src[i + c] = UInt8(Double(src[i + c]) * a) }   // premultiplied: scale all four
    }
} }
let srcCtx = CGContext(data: &src, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: rgb,
                       bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let art = srcCtx.makeImage()!
let out = CGContext(data: nil, width: Int(S), height: Int(S), bitsPerComponent: 8, bytesPerRow: Int(S) * 4, space: rgb,
                    bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
out.setFillColor(CGColor(srgbRed: CGFloat(bg.0) / 255, green: CGFloat(bg.1) / 255, blue: CGFloat(bg.2) / 255, alpha: 1))
out.fill(CGRect(x: 0, y: 0, width: S, height: S))
out.interpolationQuality = .high
let side = Double(w) * k
out.draw(art, in: CGRect(x: (S - side) / 2, y: (S - side) / 2, width: side, height: side))
let icon = out.makeImage()!
func save(_ img: CGImage, _ url: URL) {
    let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, img, nil); CGImageDestinationFinalize(dest)
}
save(icon, outURL)

// Preview: the icon under iOS's mask on a grey page, to look at.
let pv = CGContext(data: nil, width: 1200, height: 1200, bitsPerComponent: 8, bytesPerRow: 1200 * 4, space: rgb,
                   bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
pv.setFillColor(CGColor(srgbRed: 0.85, green: 0.86, blue: 0.88, alpha: 1)); pv.fill(CGRect(x: 0, y: 0, width: 1200, height: 1200))
let rect = CGRect(x: 88, y: 88, width: S, height: S)
pv.addPath(CGPath(roundedRect: rect, cornerWidth: R, cornerHeight: R, transform: nil)); pv.clip()
pv.draw(icon, in: rect)
save(pv.makeImage()!, previewURL)
print("source \(w)x\(h) bg=\(bg) edge-stray=\(edgeMax) content-box=(\(minX),\(minY))-(\(maxX),\(maxY)) points=\(content.count)")
print("scale k=\(String(format: "%.4f", k)) artwork side=\(Int(side)) px of 1024 (full bleed would be \(String(format: "%.4f", S / Double(w))))")
