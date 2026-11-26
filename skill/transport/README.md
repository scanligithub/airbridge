# Generic Skill Transport

This package contains the first MVP implementation of the EDA-neutral Transport Contract.

- `contract.py`: shared data types and the `TransportProvider` interface.
- `fake_provider.py`: in-memory Provider for isolated Skill tests.
- `airbridge_provider.py`: Host-A Provider mapping the Contract to the existing local AirBridge HTTP API.

The current MVP supports generic Shell execution and read-only lookup of the same request ID. Filesystem methods and EDA-specific lifecycle operations are intentionally outside this first implementation.

Both Providers implement the same interface. Fake Provider requires no AirBridge.exe, serial link, Host B, or EDA software. AirBridge Provider requires the existing Host-A AirBridge service and an Agent token supplied through the existing environment-variable mechanism; it does not provision or modify credentials.

Request IDs are UUIDs because the current AirBridge request tracker requires UUID correlation. A timeout or indeterminate HTTP result is returned as UNKNOWN; the Provider never automatically resubmits a command with a new ID.

Run tests from the repository root:

```sh
python -m unittest discover -s test -p "test_transport_provider.py" -v
python -m unittest discover -s test -p "test_airbridge_provider.py" -v
```
