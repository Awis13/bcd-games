// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers
// crop.swift <new logo> <current icon> <outdir>
let a = CommandLine.arguments
func load(_ p: String) -> CGImage { CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithURL(URL(fileURLWithPath: p) as CFURL, nil)!, 0, nil)! }
let logo = load(a[1]), current = load(a[2]), out = URL(fileURLWithPath: a[3])
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
func square(_ img: CGImage, cx: CGFloat, cy: CGFloat, side: CGFloat) -> CGImage {   // cy from the top
    let ctx = CGContext(data: nil, width: 1024, height: 1024, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    ctx.interpolationQuality = .high
    let k = 1024 / side, W = CGFloat(img.width), H = CGFloat(img.height)
    // Draw the whole image so that the square [cx-side/2, cy-side/2, side] fills the canvas.
    ctx.draw(img, in: CGRect(x: -(cx - side / 2) * k, y: -(H - (cy + side / 2)) * k, width: W * k, height: H * k))
    return ctx.makeImage()!
}
func write(_ img: CGImage, _ name: String) {
    let d = CGImageDestinationCreateWithURL(out.appendingPathComponent(name) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(d, img, nil); CGImageDestinationFinalize(d)
}
let cx: CGFloat = 627.5, cy: CGFloat = 615
let variants: [(String, CGImage)] = [
    ("D (now)", current),
    ("New as is", square(logo, cx: 627, cy: 627, side: 1254)),
    ("New, on frame", square(logo, cx: cx, cy: cy, side: 1096)),
    ("New, inside", square(logo, cx: cx, cy: cy, side: 1030)),
]
for (i, (_, img)) in variants.enumerated() where i > 0 { write(img, "New-\(i).png") }
let tile: CGFloat = 240, gap: CGFloat = 70, labelH: CGFloat = 60
let width = gap + CGFloat(variants.count) * (tile + gap), height = tile + 2 * gap + labelH
let c = CGContext(data: nil, width: Int(width), height: Int(height), bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let wall = CGGradient(colorsSpace: cs, colors: [CGColor(red: 0.33, green: 0.26, blue: 0.55, alpha: 1),
                                               CGColor(red: 0.22, green: 0.17, blue: 0.40, alpha: 1)] as CFArray, locations: [0, 1])!
c.drawLinearGradient(wall, start: .zero, end: CGPoint(x: 0, y: height), options: [])
for (i, (name, img)) in variants.enumerated() {
    let x = gap + CGFloat(i) * (tile + gap), rect = CGRect(x: x, y: labelH + gap, width: tile, height: tile)
    c.saveGState()
    c.addPath(CGPath(roundedRect: rect, cornerWidth: tile * 0.2237, cornerHeight: tile * 0.2237, transform: nil)); c.clip()
    c.interpolationQuality = .high; c.draw(img, in: rect); c.restoreGState()
    let s = NSAttributedString(string: name, attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica-Bold" as CFString, 24, nil),
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(red: 1, green: 1, blue: 1, alpha: 1)])
    let line = CTLineCreateWithAttributedString(s)
    c.textPosition = CGPoint(x: x + (tile - CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))) / 2, y: 24)
    CTLineDraw(line, c)
}
write(c.makeImage()!, "new-logo-preview.png")
print("done")
