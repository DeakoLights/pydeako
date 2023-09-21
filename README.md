# pydeako

## Description

`pydeako` is a Python library for discovering, connecting to, and interacting with Deako smart devices on the local network.

## Installation

`pip install pydeako`

## Usage

### `pydeako` mdns discovery client

```
import asyncio
from pydeako import discover

async def _discover():
    d = discover.DeakoDiscoverer()
    try:
        address = await d.get_address()
        print(f"Found deako device at {address}!")
    except discover.DevicesNotFoundException:
        print("No devices found!")
        pass

if __name__ == "__main__":
    asyncio.run(_discover())
```

### `pydeako` socket client

```
import asyncio
from pydeako import deako, discover

async def _discover():
    client_name = "MyClient"
    d = discover.DeakoDiscoverer()
    deako_client = deako.Deako(d.get_address, client_name=client_name)

    await deako_client.connect()
    await deako_client.find_devices()

    devices = deako_client.get_devices()

    # turn on all devices
    for uuid in devices:
        await deako_client.control_device(uuid, True)

if __name__ == "__main__":
    asyncio.run(_discover())
```

## Contributing

### Dev environment setup

1. Use Python 3.11+
2. `python -m venv venv`
3. `source venv/bin/activate`
4. `pip install -r requirements.txt`
5. `pip install -r requirements_test.txt`

### Checks

1. `pylint pydeako`
2. `pycodestyle pydeako`
3. `pytest pydeako`

## License

MIT License, see LICENSE.txt

## Project status

Actively maintained by Deako.
