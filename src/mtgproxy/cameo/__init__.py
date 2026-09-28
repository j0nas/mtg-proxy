"""Our own Cameo 5 Alpha driver: GP-GL over Bluetooth LE or USB, with registration done deliberately.

Replaces the vendored inkscape-silhouette for cut-proxies. Layers, each testable on its own:

- ``geometry``: a page-sized SVG → ordered, multi-pass polylines in page millimetres.
- ``protocol``: the GP-GL command strings and reply parsing (pure; no I/O).
- ``transport``: bytes to and from the machine (BLE, USB, or a recording stand-in for dry runs).
- ``session``: one job end to end: preflight, setup, registration scan, cut, return to origin.
"""
