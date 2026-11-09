"""No-UI macOS Keychain adapter; never logs or persists key material."""
import ctypes


def keyring_password(browser, logger):
    # Only the existing Chrome item, in the user's default search list. Do not
    # unlock a keychain, change ACLs, or fall back to the prompting CLI helper.
    if browser != 'Chrome':
        raise RuntimeError('unsupported-keychain-item')
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    uint = ctypes.c_uint32
    pointer = ctypes.c_void_p
    security.SecKeychainGetUserInteractionAllowed.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
    security.SecKeychainFindGenericPassword.argtypes = [pointer, uint, ctypes.c_char_p,
        uint, ctypes.c_char_p, ctypes.POINTER(uint), ctypes.POINTER(pointer), pointer]
    security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
    previous = ctypes.c_ubyte()
    if security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)) != 0:
        raise RuntimeError('keychain-no-ui-unavailable')
    if security.SecKeychainSetUserInteractionAllowed(False) != 0:
        raise RuntimeError('keychain-no-ui-unavailable')
    length, content = uint(), pointer()
    try:
        service, account = b'Chrome Safe Storage', b'Chrome'
        status = security.SecKeychainFindGenericPassword(None, len(service), service,
            len(account), account, ctypes.byref(length), ctypes.byref(content), None)
        if status != 0:
            raise RuntimeError('keychain-existing-access-required')
        return ctypes.string_at(content, length.value)
    finally:
        if content.value:
            security.SecKeychainItemFreeContent(None, content)
        security.SecKeychainSetUserInteractionAllowed(previous.value)
