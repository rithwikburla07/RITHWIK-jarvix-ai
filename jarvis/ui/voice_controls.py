"""Qt microphone capture, editable recognition and optional Windows speech."""
from __future__ import annotations

from array import array
from math import sqrt
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from ..voice.backends import SpeechRecognizer, VoiceError, available_voices, speak, wake_command

CAPTURE_MS = 6500


class VoiceTask(QRunnable):
    def __init__(self, operation, result, token):
        super().__init__()
        self.operation, self.result, self.token = operation, result, token

    def run(self):
        try:
            value = self.operation()
        except VoiceError as exc:
            self.result.emit(self.token, None, str(exc))
        except Exception as exc:
            self.result.emit(self.token, None, f"Audio operation failed ({type(exc).__name__}).")
        else:
            self.result.emit(self.token, value, "")


class VoiceControls(QObject):
    text_ready = Signal(str)
    info = Signal(str)
    state = Signal(str)
    level = Signal(float)
    voice_list = Signal(object)
    recognized = Signal(object, object, str)
    spoken = Signal(object, object, str)
    queried = Signal(object, object, str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.recognizer = SpeechRecognizer()
        self.recognition = QThreadPool(self)
        self.recognition.setMaxThreadCount(1)
        self.synthesis = QThreadPool(self)
        self.synthesis.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.finish)
        self.audio = self.stream = self.format = None
        self.buffer = bytearray()
        self.cancel = None
        self.speech_cancel = None
        self.wake = False
        self.awake = False
        self.recognized.connect(self._recognized)
        self.spoken.connect(self._spoken)
        self.queried.connect(self._voices)

    @property
    def listening(self):
        return self.audio is not None

    @staticmethod
    def audio_format(device):
        for channels, rate in ((1, 16000), (1, 48000), (1, 44100), (2, 48000), (2, 44100)):
            fmt = QAudioFormat()
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            fmt.setSampleRate(rate)
            fmt.setChannelCount(channels)
            if device.isFormatSupported(fmt):
                return fmt
        return None

    def push(self):
        if self.listening:
            self.finish()
        elif self.cancel is not None:
            self.stop()
            self.info.emit("Recognition cancelled.")
        else:
            self.start()

    def start_wake(self):
        if self.wake:
            self.stop()
            self.info.emit("Wake-word listening stopped.")
            return
        if self.settings.get("voice_provider") != "vosk":
            self.info.emit("Wake-word listening requires offline Vosk.")
            return
        if not self.settings.get("wake_word"):
            self.info.emit("Enable offline wake-word mode in Settings first.")
            return
        self.wake = True
        self.start()

    def start(self):
        if self.settings.get("voice_provider") == "off":
            self.info.emit("Select a microphone provider in Settings.")
            return
        if self.cancel is not None or self.listening:
            return
        if self.speech_cancel is not None:
            self.speech_cancel.set()
        device = QMediaDevices.defaultAudioInput()
        fmt = None if device.isNull() else self.audio_format(device)
        if fmt is None:
            self.wake = False
            self.info.emit("No compatible microphone was found. Check Windows sound input.")
            return
        self.buffer.clear()
        self.format = fmt
        audio = QAudioSource(device, fmt, self)
        audio.setBufferSize(fmt.bytesForDuration(250_000))
        self.audio = audio
        stream = self.stream = audio.start()
        if stream is not None:
            stream.readyRead.connect(self._read)
        if stream is None or audio.error().name != "NoError":
            self._close_stream()
            self.wake = False
            self.info.emit("Windows could not start the microphone. Check its input permissions.")
            return
        self.state.emit("Listening")
        self.timer.start(CAPTURE_MS)

    def _read(self):
        if self.stream is None:
            return
        raw = bytes(self.stream.readAll())
        if not raw:
            return
        size = self.format.bytesPerFrame()
        raw = raw[:len(raw) - len(raw) % size]
        if not raw:
            return
        samples = memoryview(raw).cast("h")
        if self.format.channelCount() == 2:
            mono = array("h", ((left + right) // 2 for left, right in zip(samples[::2], samples[1::2])))
            raw, samples = mono.tobytes(), mono
        rms = sqrt(sum(sample * sample for sample in samples) / len(samples))
        self.level.emit(min(1., rms / 7000))
        remaining = self.format.sampleRate() * 2 * 7 - len(self.buffer)
        self.buffer.extend(raw[:remaining])

    def _close_stream(self):
        self.timer.stop()
        audio, stream = self.audio, self.stream
        self.audio = self.stream = None
        if stream is not None:
            stream.readyRead.disconnect(self._read)
        if audio is not None:
            audio.stop()
            audio.deleteLater()
        self.level.emit(0.)

    def finish(self):
        if not self.listening:
            return
        self._read()
        rate, pcm = self.format.sampleRate(), bytes(self.buffer)
        self.buffer.clear()
        self._close_stream()
        provider, model, language = (self.settings.get(key) for key in ("voice_provider", "vosk_model", "language"))
        self.cancel = token = Event()
        job = VoiceTask(lambda: self.recognizer.recognize(pcm, rate, provider, model, language, token),
                        self.recognized, token)
        if not self.recognition.tryStart(job):
            self.cancel = None
            self.info.emit("Recognition is finishing a previous recording.")
            self.state.emit("Idle")
            return
        self.state.emit("Thinking")

    def _recognized(self, token, text, error):
        if token is not self.cancel or token.is_set():
            return
        self.cancel = None
        if error:
            self.wake = False
            self.info.emit(error)
        elif self.wake:
            command = text if self.awake else wake_command(text or "")
            self.awake = command == "" if command is not None else False
            if command:
                self.wake = False
                self.text_ready.emit(command)
            else:
                if self.awake:
                    self.info.emit("Wake word heard. Say a command.")
                QTimer.singleShot(140, self._continue_wake)
        elif text:
            self.text_ready.emit(text)
        else:
            self.info.emit("No speech was recognized.")
        self.state.emit("Idle")

    def _continue_wake(self):
        if self.wake:
            self.start()

    def say(self, text, *, test=False):
        if not text or self.listening or (not test and not self.settings.get("speech")):
            return
        if self.speech_cancel is not None:
            return
        voice, rate, volume = (self.settings.get(key) for key in ("speech_voice", "speech_rate", "speech_volume"))
        self.speech_cancel = token = Event()
        if self.synthesis.tryStart(VoiceTask(lambda: speak(text, voice, rate, volume, token), self.spoken, token)):
            self.state.emit("Speaking")
        else:
            self.speech_cancel = None

    def _spoken(self, token, _value, error):
        if token is self.speech_cancel:
            self.speech_cancel = None
            if not token.is_set():
                if error:
                    self.info.emit(error)
                self.state.emit("Idle")

    def detect_voices(self):
        if not self.synthesis.tryStart(VoiceTask(available_voices, self.queried, None)):
            self.info.emit("Speech output is busy.")

    def _voices(self, _token, result, error):
        if error:
            self.info.emit(error)
        else:
            self.voice_list.emit(result)

    def stop(self):
        self.wake = self.awake = False
        self._close_stream()
        self.buffer.clear()
        for token in (self.cancel, self.speech_cancel):
            if token is not None:
                token.set()
        self.cancel = self.speech_cancel = None
        self.state.emit("Idle")

    def shutdown(self):
        self.stop()
        self.recognition.waitForDone()
        self.synthesis.waitForDone()
        self.recognizer.clear_model()
