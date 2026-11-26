# Generic Skill Transport

This package is the first minimal implementation of the EDA-neutral Transport Contract.

- `contract.py`: shared data types and the `TransportProvider` interface.
- `fake_provider.py`: in-memory provider for Skill tests; it does not contact AirBridge, open a serial port, start a process, or require EDA software.

The first MVP implements only generic Shell execution. Filesystem methods and the real AirBridge Provider are intentionally separate follow-up steps.

Run the tests from the repository root:

```sh
python -m unittest discover -s test -p "test_transport_provider.py" -v
```
