"""Speak literal text through Readio's VoiceRender-backed Python facade."""

from readio.api import speak


def main() -> None:
    speak(
        "A short sentence spoken through the Readio Python API.",
        voice="kokoro:v1.0/af_heart",
        language="en-US",
    )


if __name__ == "__main__":
    main()
