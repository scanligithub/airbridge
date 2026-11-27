# Generic Skill Transport

This package contains the first MVP implementation of the EDA-neutral Transport Contract.

- `contract.py`: shared data types and the `TransportProvider` interface.
- `fake_provider.py`: in-memory Provider for isolated Skill tests.
- `stdio_client.py`: minimal MCP STDIO client for the public AirBridge Gateway tool contract.
- `airbridge_provider.py`: Host-A Provider mapping the Contract to the credential-managed AirBridge MCP STDIO Gateway.

The current MVP supports generic Shell execution and read-only lookup of the same request ID. Filesystem methods and EDA-specific lifecycle operations are intentionally outside this first implementation.

Both Providers implement the same interface. Fake Provider requires no AirBridge.exe, serial link, Host B, or EDA software. AirBridge Provider starts the installed `AirBridge.exe mcp stdio` Gateway as a local child process, passing only the non-secret `AIRBRIDGE_AGENT_ID`. The STDIO child loads its Agent credential internally from Windows Credential Manager; the Python Skill process never reads or receives the Token. Configure the non-secret executable path with `AIRBRIDGE_EXE_PATH` or the Provider's `executable_path` argument. The Provider does not provision or modify credentials.

Request IDs are UUIDs because the current AirBridge request tracker requires UUID correlation. A timeout or indeterminate MCP STDIO result is returned as UNKNOWN; the Provider never automatically resubmits a command with a new ID.

Run tests from the repository root:

```sh
python -m unittest discover -s test -p "test_transport_provider.py" -v
python -m unittest discover -s test -p "test_airbridge_provider.py" -v
```
