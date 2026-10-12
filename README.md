# Horizyn 0.1.35 runtime observations

Manually dispatched macOS tests of the public, unmodified Horizyn 0.1.35 release. The workflow verifies the release checksum and original code signature, launches the actual application, and records screenshots and runtime results.

`ui.swift` is an external macOS accessibility/keyboard driver. It does not replace, rebuild, or patch Horizyn. A temporary HTML page and Git repository provide disposable test content. No Windows product source or personal files are included.

The job has an eight-minute timeout and artifacts expire after one day. A successful workflow means evidence collection finished; check `report.json`, screenshots, and individual results before treating an interaction as verified. No recurring workflow is configured.
