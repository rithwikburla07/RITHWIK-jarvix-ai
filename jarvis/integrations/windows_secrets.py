"""Current-user Windows DPAPI protection for application identity bytes."""
import ctypes
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte)))


def protect(data, *, decrypt=False):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = Blob(len(data), source)
    result = Blob()
    if decrypt:
        function = crypt.CryptUnprotectData
        second = ctypes.c_void_p
    else:
        function = crypt.CryptProtectData
        second = wintypes.LPCWSTR
    function.argtypes = (ctypes.POINTER(Blob), second, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob))
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(result.pbData)
