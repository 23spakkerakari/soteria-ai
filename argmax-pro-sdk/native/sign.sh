#!/usr/bin/env bash
# Sign the soteria-transcribe binary with a stable code-signing identity so macOS
# stops asking for the Keychain (login) password on every transcription.
#
# Why: Argmax caches its offline license in the macOS Keychain. The access
# decision is tied to the binary's signing identity, and a `swift build` binary
# is only ad-hoc signed, so macOS re-prompts each time it touches the license.
#
# Prerequisite (once): create a self-signed code-signing certificate named
# "Soteria Dev" — Keychain Access → Certificate Assistant → Create a Certificate:
#   Name: "Soteria Dev" · Identity Type: "Self Signed Root" · Certificate Type: "Code Signing"
#
# Run this after ANY `swift build` of soteria-transcribe, because rebuilding
# replaces the binary with a fresh ad-hoc signature.
set -euo pipefail
cd "$(dirname "$0")"

BIN=".build/release/soteria-transcribe"
IDENT="${SOTERIA_SIGN_IDENTITY:-Soteria Dev}"

if [ ! -x "$BIN" ]; then
  echo "Binary not found: $BIN — build it first:"
  echo "  swift build -c release --product soteria-transcribe"
  exit 1
fi

if ! security find-identity -v -p codesigning | grep -q "$IDENT"; then
  echo "No code-signing identity named \"$IDENT\" found."
  echo "Create one: Keychain Access → Certificate Assistant → Create a Certificate →"
  echo "  Name: $IDENT · Identity Type: Self Signed Root · Certificate Type: Code Signing"
  exit 1
fi

codesign --force --sign "$IDENT" --identifier com.soteria.soteria-transcribe "$BIN"
codesign --verify --verbose "$BIN"
echo "Signed $BIN with identity \"$IDENT\"."
