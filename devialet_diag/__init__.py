"""Non-destructive network / BLE diagnostic for Devialet Phantom speakers.

Every probe in this package is read-only: mDNS browsing, passive BLE
advertisement scanning, passive UDP listening, TCP connect checks and
(optionally) HTTP GET requests. Nothing here changes pairing, stereo
configuration or any speaker setting.
"""

__version__ = "0.1.0"
