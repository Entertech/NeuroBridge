from ..domain.capabilities import ProfileCapabilities

PROFILE_ID = "ubuntu_headband_wired"
CAPABILITIES = ProfileCapabilities(True, True, False, True, 1, frozenset({"status", "eeg", "hr", "eeg.raw", "hr.raw"}))
