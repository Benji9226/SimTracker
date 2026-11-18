"""The version of this program, and where new versions are published.

To publish an update: raise VERSION, build the exe (python tools/build_exe.py), then on GitHub create a new
release whose tag is the same number (for example v1.1.0) and attach dist/SimTracker.exe to it.
The program checks that release page when it starts; if the tag is higher than VERSION, a button
"Update application" appears in the dashboard.
"""
VERSION = "1.0.1"
UPDATE_REPO = "Benji9226/SimTracker"      # github.com/<owner>/<name>: its latest release is what the update check looks at
ASSET_NAME = "SimTracker.exe"             # the file to download from that release
