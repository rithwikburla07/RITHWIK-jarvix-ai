"""Bounded recognition and speech synthesis. Capture is supplied by the UI."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import time
from pathlib import Path

_WAKE = re.compile(r"^\s*(?:(?:hey|ok|okay|hi|hello)\s+)?(?:jarvis|jarvix)\b[\s,:;.!-]*", re.I)

RECOGNITION_LOCALES = {
    "en": "en-IN", "hi": "hi-IN", "ru": "ru-RU", "uk": "uk-UA",
    "pt": "pt-BR", "tr": "tr-TR", "it": "it-IT", "de": "de-DE",
}


class VoiceError(RuntimeError):
    pass


def wake_command(text):
    match = _WAKE.match(text)
    return None if match is None else text[match.end():].strip()


def model_present(path):
    if not path or not os.path.isabs(path) or path.startswith(("\\\\", "//")):
        return False
    root = Path(path)
    return (root / "am" / "final.mdl").is_file() and (root / "conf" / "model.conf").is_file()


class SpeechRecognizer:
    def __init__(self):
        self.cached = None
        self.path = None

    def recognize(self, pcm, rate, provider, model, language, cancel):
        if cancel.is_set():
            return ""
        if len(pcm) < rate // 5:
            raise VoiceError("No speech was recorded.")
        if provider == "google":
            try:
                import speech_recognition as sr
            except ImportError as exc:
                raise VoiceError("Install the optional Google speech recognition package first.") from exc
            recognizer = sr.Recognizer()
            recognizer.operation_timeout = 8
            try:
                return recognizer.recognize_google(
                    sr.AudioData(pcm, rate, 2), language=RECOGNITION_LOCALES.get(language, "en-IN")
                )
            except sr.UnknownValueError as exc:
                raise VoiceError("Speech was unclear. Try again.") from exc
            except (sr.RequestError, OSError) as exc:
                raise VoiceError("Google speech recognition is unavailable. Check your connection.") from exc
        if provider != "vosk":
            raise VoiceError("Select a speech provider in Settings.")
        if not model_present(model):
            raise VoiceError("Choose an extracted Vosk model folder in Settings.")
        try:
            import vosk
        except ImportError as exc:
            raise VoiceError("Install the optional Vosk package first.") from exc
        if self.path != model:
            vosk.SetLogLevel(-1)
            self.cached = vosk.Model(model)
            self.path = model
        recognizer = vosk.KaldiRecognizer(self.cached, rate)
        words = []
        for offset in range(0, len(pcm), 4000):
            if cancel.is_set():
                return ""
            if recognizer.AcceptWaveform(pcm[offset:offset + 4000]):
                words.append(json.loads(recognizer.Result()).get("text", ""))
        words.append(json.loads(recognizer.FinalResult()).get("text", ""))
        return " ".join(filter(None, words)).strip()

    def clear_model(self):
        self.path = self.cached = None


def available_voices():
    try:
        import pyttsx3
    except ImportError as exc:
        raise VoiceError("Install the optional Windows speech output package first.") from exc
    engine = pyttsx3.init(driverName="sapi5")
    return [(voice.id, voice.name) for voice in engine.getProperty("voices")]


def speak(text, voice, rate, volume, cancel):
    try:
        import pyttsx3
    except ImportError as exc:
        raise VoiceError("Install the optional Windows speech output package first.") from exc
    engine = pyttsx3.init(driverName="sapi5")
    voices = engine.getProperty("voices")
    if voice and voice not in {item.id for item in voices}:
        raise VoiceError("The selected Windows voice is unavailable.")
    if voice:
        engine.setProperty("voice", voice)
    engine.setProperty("rate", rate)
    engine.setProperty("volume", volume / 100)
    engine.say(text[:450])
    engine.startLoop(False)
    try:
        deadline = time.monotonic() + 20
        while not cancel.is_set() and time.monotonic() < deadline:
            engine.iterate()
            if not engine.isBusy():
                break
            cancel.wait(.02)
    finally:
        engine.stop()
        engine.endLoop()


def dependency_status(settings):
    provider = settings.get("voice_provider")
    if provider == "google":
        status = ("Google recognition selected · recordings leave this PC when requested"
                  if importlib.util.find_spec("speech_recognition") else "Google recognition package missing")
    elif provider == "vosk":
        status = ("Vosk model configured" if model_present(settings.get("vosk_model")) else "Vosk model missing")
        if not importlib.util.find_spec("vosk"):
            status += " · Vosk package missing"
    else:
        status = "Microphone capture off"
    tts = "Windows speech output package available" if importlib.util.find_spec("pyttsx3") else "Windows speech output package missing"
    return status, tts
