"""Offline model download policy and safe extraction tests."""
from threading import Event
from zipfile import ZipFile

import pytest

from jarvis.voice.backends import VoiceError
from jarvis.voice.models import extract_model, verify_request

NAME = "vosk-model-small-en-test"
URL = "https://alphacephei.com/vosk/models/" + NAME + ".zip"


def test_offline_model_requires_official_zip_and_expected_digest():
    assert verify_request(URL, "a" * 64) == NAME
    with pytest.raises(VoiceError):
        verify_request(URL, "wrong")
    with pytest.raises(VoiceError):
        verify_request(URL.replace("alphacephei.com", "example.com"), "a" * 64)


@pytest.mark.parametrize("path", [NAME + "/../../private.txt", NAME + "/NUL.txt"])
def test_model_rejects_unsafe_archive_entries(tmp_path, path):
    zip_path = tmp_path / "model.zip"
    with ZipFile(zip_path, "w") as file:
        file.writestr(path, "disposable fixture")
    with pytest.raises(VoiceError, match="unsafe"):
        extract_model(zip_path, tmp_path, NAME, Event())
