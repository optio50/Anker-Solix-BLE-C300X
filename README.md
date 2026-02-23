# Anker Solix C300X — Bluetooth Power Monitor

A real-time desktop GUI for monitoring an Anker Solix C300/C300X power station over Bluetooth, with no cloud account or Anker app required.
Thanks to Flip-Dots for the hard work developing the [SolixBLE](https://github.com/flip-dots/SolixBLE) library.
I previously had this script working pretty good last year mostly bymyself and made a mess of it trying to get it to work after I upgraded the firmware (BTW, dont do that) confirmed working on v1.0.5.1.
It was such a mess I decided to let copilot have a crack at it and its working well as of today.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey)

---

## Features

- 📊 Live scrolling graphs for all power channels (AC in/out, DC, Solar, USB-C x3, USB-A)
- 🔋 Battery percentage with time-remaining estimate
- 💡 Light bar status
- 🔌 Port connection status for all outputs
- 🔄 Automatic BLE device discovery — no MAC address configuration needed
- 🔒 Supports encrypted telemetry (Anker firmware v3+ / post-2025)
- 📅 Date/time X-axis on all graphs
- 🖱️ Clickable labels jump to the relevant graph tab

---

## Supported Devices

| Device | Status |
|--------|--------|
| Anker Solix C300X | ✅ Confirmed working |
| Anker Solix C300 | ✅ Should work (same protocol) |
| Anker Solix C1000 | ⚠️ Change class to `C1000` (see below) |

> **Note:** No Bluetooth pairing is required. The device advertises telemetry openly and the script negotiates its own encrypted session.

---

## Requirements

### Operating System

- **Linux** (BlueZ Bluetooth stack required — Ubuntu 20.04+, Debian, Arch, etc.)
- Windows and macOS may work via Bleak but are untested with this script

### Python

- **Python 3.11 or newer**

Check your version:
```bash
python3 --version
```

### Bluetooth

- A Bluetooth 4.0+ adapter accessible via BlueZ
- The Solix device must be **within BLE range** (~10 metres)
- No pairing needed

---

## Installation

### 1. Clone or download the script

```bash
git clone https://github.com/optio50/Anker-Solix-BLE-C300X
cd Anker-Solix-BLE-C300X
```

Or just download `Anker-Power-Monitor-Clickable.py` directly.

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install \
  SolixBLE \
  bleak \
  bleak-retry-connector \
  cryptography \
  pycryptodome \
  PyQt5 \
  pyqtgraph \
  pglive \
  qasync \
  numpy
```

#### Confirmed working versions

| Package | Version tested |
|---------|---------------|
| `SolixBLE` | 3.0.0 |
| `bleak` | 1.0.1 |
| `bleak-retry-connector` | 4.0.0 |
| `cryptography` | 46.0.5 |
| `pycryptodome` | 3.23.0 |
| `PyQt5` | 5.15.11 |
| `pyqtgraph` | 0.13.7 |
| `pglive` | 0.9.0 |
| `qasync` | 0.27.1 |
| `numpy` | 2.3.1 |

### 4. (Linux only) Bluetooth permissions

On most Linux systems, Bluetooth requires either `root` or membership in the `bluetooth` group:

```bash
sudo usermod -aG bluetooth $USER
```

Log out and back in for the group change to take effect. If you still get permission errors, you can run once with `sudo` to confirm it works, then fix permissions properly.

---

## Usage

### Auto-discover (recommended)

The script will scan for any nearby Solix device and connect to the first one found:

```bash
./Anker-Power-Monitor-Clickable.py
```

Or:

```bash
python3 Anker-Power-Monitor-Clickable.py
```

### Target a specific device by MAC address

If you have multiple Solix devices nearby, pass the MAC address as an argument:

```bash
./Anker-Power-Monitor-Clickable.py AA:BB:CC:DD:EE:FF
```

The device MAC address can be found with:

```bash
# Install bluetooth tools if needed: sudo apt install bluez
bluetoothctl scan on
# Look for a device named "C300X" or similar, note its address
```

---

## Connection sequence

When you launch the script, the status label in the top-left cycles through:

| Label | Meaning |
|-------|---------|
| `Scanning...` | Searching for Solix BLE devices (5 second window) |
| `Negotiating...` | ECDH key exchange + AES session setup (30–90 seconds) |
| `Connected` | Receiving live telemetry — graphs begin updating |
| `No Device Found` | Nothing discovered in scan window — check range/adapter |
| `Disconnected` | Lost connection — script will auto-reconnect |

> **The negotiation step takes 30–90 seconds on first connect.** This is normal — Anker's firmware requires a multi-step encrypted handshake before any data is sent.

---

## C1000 Users

Change one line in the script (around line 334):

```python
# Change this:
self.solix_device = C300(target_device)

# To this:
self.solix_device = C1000(target_device)
```

Note: the C1000 does not have USB-C3, USB-A, DC out, or a light bar, so those displays will show default/zero values.

---

## Troubleshooting

**"No Device Found" / scan finds nothing**
- Make sure the Solix is powered on and not already connected to another BLE client (phone app, Home Assistant, etc.)
- Only one BLE client can connect at a time — close the Anker app first
- Try moving closer to the unit

**Stays on "Negotiating..." indefinitely**
- The encryption handshake timed out — this can happen if the device was mid-session with another client. Wait ~30 seconds for the device to drop the old session, then restart the script

**Graphs show no data after connecting**
- Check that `SolixBLE` v3.0.0+ is installed: `pip show SolixBLE`
- Versions before 3.0.0 do not support encrypted telemetry and will connect but receive no parseable data

**Permission denied on Bluetooth**
- Run `sudo python3 Anker-Power-Monitor-Clickable.py` once to confirm it's a permissions issue
- Then fix with: `sudo usermod -aG bluetooth $USER` and re-login

**PyQt5 / display errors on headless systems**
- This script requires a desktop display. Set `DISPLAY=:0` if running via SSH with X forwarding

---

## Dependencies & credits

- [SolixBLE](https://github.com/flip-dots/SolixBLE) — BLE communication and encrypted telemetry parsing by Harvey Lelliott (flip-dots). Installed via `pip install SolixBLE` — **no git clone required, the PyPI package is used unmodified**.
- [Bleak](https://github.com/hbldh/bleak) — cross-platform BLE library
- [pglive](https://github.com/domarm-comat/pglive) — live PyQtGraph plotting
- [qasync](https://github.com/Matthewacon/qasync) — asyncio integration for Qt
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) — Qt GUI framework
- [pyqtgraph](https://www.pyqtgraph.org/) — fast scientific plotting
- <img width="2400" height="1600" alt="Screenshot at 2026-02-22 20-55-08" src="https://github.com/user-attachments/assets/9ea9f8f8-9de0-4db8-97f6-686a2e7a8a9b" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 20-39-54" src="https://github.com/user-attachments/assets/f3fee363-e623-4e9e-b556-e9c2846fc906" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 20-37-48" src="https://github.com/user-attachments/assets/beacc17b-4961-49c9-81d6-8b8547718f71" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 19-16-54" src="https://github.com/user-attachments/assets/4085729c-e61d-4fbc-9b19-7ca60a21c242" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 19-14-55" src="https://github.com/user-attachments/assets/8e0019ab-924b-4584-8ab4-91cf8bb1d130" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 19-13-46" src="https://github.com/user-attachments/assets/45923ecd-6855-4483-9014-18239da1d361" />
<img width="2400" height="1600" alt="Screenshot at 2026-02-22 19-13-11" src="https://github.com/user-attachments/assets/cc6d989c-8fb1-45a6-807c-e0f2095cf12e" />
<img width="2412" height="1654" alt="Screenshot at 2026-02-22 19-12-19" src="https://github.com/user-attachments/assets/76c9f261-904e-42d8-93c5-f7ba21b79e24" />

