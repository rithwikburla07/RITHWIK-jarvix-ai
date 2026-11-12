"""System output volume and mute via pycaw (optional). Percent 0-100, always clamped."""
from __future__ import annotations


class VolumeUnavailable(Exception):
    pass


MSG = "Volume control isn't available on this PC (pycaw or the audio device was not found)."


def clamp(level) -> int:
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        raise ValueError("volume must be a number")
    return max(0, min(100, int(round(level))))


class PycawBackend:
    """Default speaker endpoint. Written against pycaw 2024+. Verify on Windows."""

    def __init__(self):
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        vol = getattr(dev, "EndpointVolume", None)
        if vol is None:
            vol = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                       POINTER(IAudioEndpointVolume))
        self._v = vol

    def get_percent(self) -> int:
        return int(round(self._v.GetMasterVolumeLevelScalar() * 100))

    def set_percent(self, p: int) -> None:
        self._v.SetMasterVolumeLevelScalar(p / 100.0, None)

    def get_mute(self) -> bool:
        return bool(self._v.GetMute())

    def set_mute(self, m: bool) -> None:
        self._v.SetMute(1 if m else 0, None)


def _default_factory():
    return PycawBackend()


class VolumeController:
    def __init__(self, backend=None, factory=_default_factory):
        self._backend, self._factory = backend, factory

    def _b(self):
        if self._backend is None:
            try:
                self._backend = self._factory()
            except Exception as exc:
                raise VolumeUnavailable(MSG) from exc
        return self._backend

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except VolumeUnavailable:
            raise
        except Exception as exc:
            raise VolumeUnavailable(MSG) from exc

    def get(self) -> int:
        return int(self._call(self._b().get_percent))

    def set(self, level) -> int:
        p = clamp(level)
        self._call(self._b().set_percent, p)
        return p

    def step(self, delta) -> int:
        return self.set(self.get() + int(delta))

    def set_mute(self, muted: bool) -> None:
        self._call(self._b().set_mute, bool(muted))
