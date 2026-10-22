# Millrace Plus tests

Default pytest collection includes all current router tests and four public package boundary modules.
Public standalone CI runs metadata, assets, package boundaries, test support boundaries and catalog tests without Core.
The full suite installs matched Core 0.23.0 and exercises native adapters with fake processes only.
The historical manifest/resource/domain-engine contracts and their tests are retired.
No retired test is counted as passing or hidden behind a skip.
