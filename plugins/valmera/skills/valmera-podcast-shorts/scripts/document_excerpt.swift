// Locate only the recorded excerpt on the actual archived crop. Vision's coordinates start at the bottom.
import AppKit
import Vision

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let phrase = CommandLine.arguments[2]
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.minimumTextHeight = 0.005
try VNImageRequestHandler(url: url).perform([request])
for observation in request.results ?? [] {
    guard let line = observation.topCandidates(1).first,
          let range = line.string.range(of: phrase, options: [.caseInsensitive, .diacriticInsensitive]),
          let match = try line.boundingBox(for: range) else { continue }
    let box = match.boundingBox
    print([box.minX, 1-box.maxY, box.maxX, 1-box.minY])
    break
}
