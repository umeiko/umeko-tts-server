"""Compatibility entry point for the TLS-verified, non-destructive deployment check.

Set UMEKO_TTS_API_KEY; optional UMEKO_TTS_CA_FILE and TTS_TEST_ADMIN_TOKEN.
Usage: python scripts/public_e2e.py https://tts.example.com/services/voice
"""

from smoke_test import main

if __name__ == "__main__":
    main()
