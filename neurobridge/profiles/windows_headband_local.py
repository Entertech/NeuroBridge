from ..domain.capabilities import ProfileCapabilities

PROFILE_ID = "windows_headband_local"
CAPABILITIES = ProfileCapabilities(True, False, True, False, 1, frozenset({"status", "eeg", "hr", "eeg.raw", "hr.raw"}))
