// Minimal file-transcription wrapper for Soteria AI.
//
// Transcribes a single audio file with the Argmax Pro SDK using the `tiny`
// Whisper model, then prints one machine-readable JSON line to stdout:
//
//     {"text": "My friend twisted their ankle while hiking."}
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

            // Explicitly use the tiny Whisper model.
            let config = WhisperKitProConfig(model: "tiny")
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
