"""Windows Credential Manager boundary. Environment/.env always takes precedence."""

import getpass
import sys


def windows_secret(service):
    if sys.platform != "win32":
        return None
    # Select the OS store explicitly; never fall back to a plaintext keyring backend.
    try:
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring().get_password(service, getpass.getuser())
    except Exception:
        # Backend errors may include a credential. Only absence crosses this boundary.
        return None
