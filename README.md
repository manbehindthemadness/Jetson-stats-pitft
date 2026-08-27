# Jetson Stats PiTFT

A fast, compact telemetry dashboard for an NVIDIA Jetson AGX Orin and the
Adafruit 1.14-inch ST7789 Mini PiTFT. It draws directly with Pillow, converts
frames to RGB565 with NumPy, and writes straight to `/dev/spidev0.0`: no X11,
Wayland, browser, or heavyweight dashboard process.

![Command deck preview](docs/previews/0-deck.png)

## What it shows

The encoder and two front buttons move through six live pages:

1. **Deck** — CPU and GPU gauges, memory, temperature, power, and fan speed
2. **CPU** — all 12 cores, aggregate utilization, load, and a sparkline
3. **GPU** — GR3D load, board power, temperature, and history
4. **Memory** — RAM, swap, and root filesystem capacity
5. **Network** — interface, address, RX/TX rates, and traffic history
6. **System** — hostname, power mode, JetPack, L4T, kernel, and uptime

Controls:

- Turn encoder: cycle backward/forward through color schemes
- Press top PiTFT button: previous page
- Press bottom PiTFT button: next page
- Press encoder: toggle automatic page rotation (eight seconds per page)

The original **Orbit** colors remain the startup default. The additional
schemes are Ember, Matrix, Synth, and Arctic. Button polarity is learned from
the idle level at service startup, so both PiTFT buttons act on press even when
their electrical idle levels differ.

The boot helper also sets the Tegra PADCTL pull-up field on all five navigation
inputs. GPIO character-device bias flags do not override a pad left in Tegra's
hardware pull-down state on this platform.

Telemetry comes from one persistent NVIDIA `tegrastats` process plus lightweight
Linux interfaces such as `/proc`, `/sys`, `statvfs`, and the network address
ioctl. Fan speed is read from the kernel hwmon tachometer because JetPack 7
`tegrastats` does not report it. Both the standard `fan*_input` layout and
NVIDIA's `pwm_tach/rpm` layout are supported without depending on an unstable
`hwmon` number. Static identity and slow-changing paths and network details are
cached. The default screen update is 2 Hz while GPIO input is sampled
independently at 200 Hz.
The SPI clock defaults to the signal-integrity-tested 4 MHz; the tiny frame is
still transferred quickly at that conservative rate.

The 135-pixel panel is centered in a 240-pixel controller dimension with an odd
remainder. The driver clears the extra controller column at X=52 before drawing
the 135 content columns at X=53, preventing a retained garbage scanline along
the rotated top edge.

For a lively instrument-panel feel, each authoritative 2 Hz sample is revealed
through semantic child regions in a shuffled sequence at 20 updates per second.
Only the affected gauge, row, or graph segment is transferred over SPI. This
perceptual refresh does not fabricate telemetry, and `--micro-fps 0` disables it.

## Supported hardware and wiring

This release targets the **Jetson AGX Orin Developer Kit, P3737 carrier plus
P3701 module**, on JetPack 7 / L4T R39. It deliberately refuses to alter live
PADCTL registers on another board.

| Function | Header pin | Tegra line | PADCTL address |
|---|---:|---|---:|
| Encoder DT / A | 31 | PAA.00 | `0x0C303010` |
| Encoder CLK / B | 37 | PAA.03 | `0x0C303008` |
| PiTFT top / previous | 33 | PAA.02 | `0x0C303000` |
| PiTFT bottom / next | 16 | PBB.01 | `0x0C303048` |
| TFT MOSI | 19 | SPI1_MOSI | configured by SPI project |
| TFT D/C | 22 | PP.04 | configured by SPI project |
| TFT SCLK | 23 | SPI1_SCLK | configured by SPI project |
| TFT CS | 24 | SPI1_CS0 | configured by SPI project |
| Encoder switch | 29 | PAA.01 | `0x0C303018` |

Viewed with the header vertical and physical pin 1 at the upper left, the final
wiring is:

```text
                 AGX ORIN J30 HEADER
                    TOP / PIN 1
             +---------------------+
       3V3  1| o                 o |2   5V
            3| o                 o |4
            5| o                 o |6   GND
            7| o                 o |8
       GND  9| o                 o |10
 DO NOT USE11| x                 x |12  DO NOT USE
           13| o                 o |14  GND
           15| o                 o |16  BOTTOM / NEXT
       3V3 17| o                 x |18  ISOLATE PiTFT CONTACT
 SPI MOSI  19| o                 o |20  GND
 SPI MISO  21| o                 o |22  TFT D/C
 SPI SCLK  23| o                 o |24  SPI CS
       GND 25| o                 o |26
           27| o                 o |28
ENCODER SW 29| o                 o |30  ENCODER GND
ENCODER DT 31| o                 o |32
TOP/PREVIOUS33| o                 o |34  GND
           35| o                 o |36
ENCODER CLK37| o                 o |38
       GND 39| o                 o |40
             +---------------------+
                         BOTTOM
```

For an encoder previously connected to the Raspberry Pi-style positions, move
DT from pin 11 to pin 31 and CLK from pin 12 to pin 37. Leave the encoder switch
on pin 29 and its ground connected to ground; pin 30 is convenient. The input
decoder rejects invalid quadrature transitions and coalesces mechanical contact
bounce so a detent produces one theme action.

Do not use physical pin 11 for an encoder phase on the AGX Orin Developer Kit.
NVIDIA marks that header position as output-only because of the base-board
signal path. Pins 31 and 37 are unused AON GPIO inputs and are the supported
encoder phase connections for this project.

The carrier also routes physical pin 18 through a one-way level shifter as
`PWM3_40PIN_LVS`, so a PiTFT button cannot drive a signal back into the SoC on
that pin. Isolate the PiTFT socket contact from the AGX pin 18 and reroute the
PiTFT `BUTTONB` signal (R3 pad 1 or its shared via) to physical pin 33. The
PiTFT's onboard 10 kOhm pull-up remains in use; do not add another pull-up. R3
pad 2 is 3.3 V and must not be used as the button-signal connection.

The signal directions above are taken from NVIDIA's
[P3737 carrier-board design resources](https://developer.nvidia.com/embedded/downloads/).
The `BUTTONA`, `BUTTONB`, R1, and R3 nets are available in Adafruit's original
[Mini PiTFT Eagle design files](https://github.com/adafruit/Adafruit-Mini-PiTFT-240x135-TFT-PCB).

The physical header's SPI1 controller appears as `/dev/spidev0.0` on this
platform. First configure its live pins with
[AGX-Orin-SPI1](https://github.com/manbehindthemadness/AGX-Orin-SPI1). A spidev
node alone does not prove that the header pads are muxed to SPI.

## Install and start at boot

```bash
git clone https://github.com/manbehindthemadness/Jetson-stats-pitft.git
cd Jetson-stats-pitft
./install.sh
```

The installer:

- installs the small Python/runtime dependencies from Ubuntu packages;
- creates `/opt/jetson-stats-pitft/venv` using system packages;
- installs a guarded boot-time helper for the five input pads;
- enables `agx-orin-pitft-input-pinmux.service`;
- enables and starts `jetson-stats-pitft.service` as the invoking user.

The SPI pinmux service from AGX-Orin-SPI1 should already be enabled. Check the
dashboard with:

```bash
systemctl status jetson-stats-pitft.service
journalctl -u jetson-stats-pitft.service -f
```

The LCD retains its last pixels without further SPI traffic. When testing,
select a visibly different page instead of treating an unchanged image as proof
that a new frame arrived:

```bash
sudo systemctl stop jetson-stats-pitft.service
sudo /opt/jetson-stats-pitft/venv/bin/jetson-stats-pitft --once --page system
sudo systemctl start jetson-stats-pitft.service
```

## Run from a checkout

On a configured Jetson:

```bash
python3 -m jetson_stats_pitft --page deck
```

Useful development options:

```bash
# Create all six mock PNGs without touching the display
python3 -m jetson_stats_pitft --preview-dir docs/previews

# Draw one distinct live frame and exit
python3 -m jetson_stats_pitft --once --page system

# Run without requesting the navigation GPIOs
python3 -m jetson_stats_pitft --no-input
```

## Design notes

The page selection is inspired by the excellent
[jetson-stats / jtop](https://github.com/rbonghi/jetson_stats) interface. This
project contains an original, purpose-built renderer and collector rather than
copying jtop code; it stays Apache-2.0 licensed and optimized for a 240×135 TFT.

The live PADCTL helpers are intentionally a boot-time bridge, not a replacement
for a board-specific device-tree configuration. Their original register values
are saved under `/run` and can be restored during the same boot:

```bash
sudo agx-orin-pitft-input-pinmux status
sudo agx-orin-pitft-input-pinmux restore
```
