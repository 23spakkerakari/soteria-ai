// Minimal file-transcription wrapper for Soteria AI.
//
// Transcribes a single audio file with the Argmax Pro SDK using the `tiny`
// Whisper model, then prints one machine-readable JSON line to stdout:
//
//     {"text": "My friend twisted their ankle while hiking."}
//
// The model is downloaded once into a persistent folder (ARGMAX_MODEL_DIR, or
// ~/Library/Caches/soteria-argmax-models) and reused from there, so transcription
// works fully offline after the first download.
//
// Exit codes: 0 success, 1 transcription/run failure, 2 usage/environment error.
// Progress and error detail go to stderr so stdout stays clean for parsing.
//
// Usage:  ARGMAX_API_KEY=ax_... soteria-transcribe <audio-file>

import Argmax
import Foundation

struct SimpleError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

@main
struct SoteriaTranscribe {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            fail("Usage: soteria-transcribe <audio-file>", 2)
        }
        let audioPath = args[1]

        guard let key = ProcessInfo.processInfo.environment["ARGMAX_API_KEY"], key.hasPrefix("ax_") else {
            fail("ARGMAX_API_KEY is not set or is not a valid ax_ key.", 2)
        }

        do {
            // Activate the Pro SDK. The license is cached in the Keychain after
            // first use, so later runs work offline.
            await ArgmaxSDK.with(ArgmaxConfig(apiKey: key))

            // Explicitly use the tiny Whisper model, stored in a persistent
            // folder so it is downloaded once and reused offline.
            let cacheBase = ProcessInfo.processInfo.environment["ARGMAX_MODEL_DIR"]
                ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
                    .appendingPathComponent("soteria-argmax-models", isDirectory: true).path
            let modelFolder = try await tinyModelFolder(cacheBase: cacheBase)

            let config = WhisperKitProConfig(modelFolder: modelFolder, download: false)
            let whisperKitPro = try await WhisperKitPro(config)

            let audioArray = try AudioProcessor.loadAudioAsFloatArray(fromPath: audioPath)

            let decodeOptions = DecodingOptions(detectLanguage: true, wordTimestamps: true, chunkingStrategy: .vad)
            let results = try await whisperKitPro.transcribe(audioArray: audioArray, decodeOptions: decodeOptions)
            let merged = WhisperKitProUtils.mergeTranscriptionResults(results)

            let text = merged.text.trimmingCharacters(in: .whitespacesAndNewlines)
            printJSON(["text": text])
        } catch {
            fail("transcription error: \(error)", 1)
        }
    }

    /// Returns the local folder that holds the tiny model, downloading it into
    /// `cacheBase` on first use and reusing it afterwards (offline-safe).
    static func tinyModelFolder(cacheBase: String) async throws -> String {
        let base = URL(fileURLWithPath: cacheBase)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)

        // Reuse an already-downloaded tiny model if it is present (the download
        // nests it under models/<repo>/<variant>, so search recursively).
        if let existing = findModelFolder(from: base) {
            return existing.path
        }

        // First run: download from the open-source WhisperKit repo.
        let folder = try await WhisperKit.download(
            variant: "tiny",
            downloadBase: base,
            from: "argmaxinc/whisperkit-coreml"
        )
        return folder.path
    }

    /// Recursively locate a folder that holds the Whisper Core ML bundles.
    static func findModelFolder(from root: URL) -> URL? {
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }
        for case let url as URL in enumerator {
            let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            guard isDir else { continue }
            if fm.fileExists(atPath: url.appendingPathComponent("MelSpectrogram.mlmodelc").path),
               fm.fileExists(atPath: url.appendingPathComponent("TextDecoder.mlmodelc").path) {
                return url
            }
        }
        return nil
    }

    static func printJSON(_ object: [String: String]) {
        do {
            let data = try JSONSerialization.data(withJSONObject: object, options: [])
            guard let json = String(data: data, encoding: .utf8) else {
                throw SimpleError("could not encode UTF-8 JSON")
            }
            FileHandle.standardOutput.write((json + "\n").data(using: .utf8)!)
        } catch {
            fail("could not serialize output: \(error)", 1)
        }
    }

    static func fail(_ message: String, _ code: Int32) -> Never {
        FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
        exit(code)
    }
}
