#!/usr/bin/env python3


import sys
import asyncio
import logging
from datetime import datetime
from collections import deque
import numpy as np
from PyQt5.QtWidgets import QMainWindow, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QTabWidget, QLCDNumber, QLabel, QApplication, QFrame
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from pglive.kwargs import Axis, Crosshair, LeadingLine
from pglive.sources.data_connector import DataConnector
from pglive.sources.live_axis import LiveAxis
from pglive.sources.live_axis_range import LiveAxisRange
from pglive.sources.live_categorized_bar_plot import LiveCategorizedBarPlot
from pglive.sources.live_plot import LiveLinePlot
from pglive.sources.live_plot_widget import LivePlotWidget
from bleak import BleakScanner
from qasync import QEventLoop
from SolixBLE import SolixBLEDevice, C300, C1000, discover_devices, PortStatus, LightStatus
import pyqtgraph as pg

# Set logging to errors only
logging.basicConfig(level=logging.ERROR)

# Custom axis for date/time formatting
class LiveDateAxis(LiveAxis):
    def __init__(self, orientation, base_timestamp=None, *args, **kwargs):
        super().__init__(orientation, *args, **kwargs)
        self.autoSISuffix = False  # Disable scale factor suffix
        self.base_timestamp = base_timestamp  # Use provided base timestamp

    def tickStrings(self, values, scale, spacing):
        if self.base_timestamp is None:
            return ["Invalid Time"] * len(values)  # Fallback if base_timestamp not set
        # Add normalized values to base_timestamp for correct display
        return [datetime.fromtimestamp(self.base_timestamp + value).strftime("%a %d %b %Y %r") for value in values]

class ClickableLabel(QLabel):
    clicked = pyqtSignal()  # Define the clicked signal

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

class ClickableLCDNumber(QLCDNumber):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

class SolixBLEGUI(QMainWindow):
    def __init__(self, device_address=None):
        super().__init__()
        self.setWindowTitle("SolixBLE Data Monitor")
        self.setGeometry(100, 100, 1200, 800)

        # Status mappings
        self.light_status_map = {
            -1: "Unknown",
            0: "Off",
            1: "Low",
            2: "Medium",
            3: "High"
        }
        self.port_status_map = {
            -1: "Unknown",
            0: "Not Connected",
            1: "Output",
            2: "Input"
        }

        # Data points
        self.numeric_data_points = [
            "ac_power_in", "ac_power_out", "usb_c1_power",
            "usb_c2_power", "usb_c3_power", "usb_a1_power",
            "dc_power_out", "solar_power_in", "power_in",
            "power_out", "battery_percentage"
        ]
        self.all_data_points = self.numeric_data_points + [
            "ac_timer_remaining", "dc_timer_remaining", "hours_remaining",
            "days_remaining", "time_remaining", "solar_port",
            "usb_port_c1", "usb_port_c2", "usb_port_c3",
            "usb_port_a1", "dc_port", "light"
        ]
        self.data = {name: deque(maxlen=86400) for name in self.numeric_data_points}
        self.time = deque(maxlen=86400)
        self.first_data_received = False
        self.base_timestamp = None  # For normalizing timestamps
        self.telemetry_logged = False  # Only dump raw telemetry once

        # BLE device
        self.device_address = device_address
        self.solix_device = None
        self.loop = None

        # Main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        main_widget.setStyleSheet("background-color: #333333;")

        # Top bar: connection status + device info
        top_bar = QHBoxLayout()
        self.connection_label = QLabel("Disconnected")
        self.connection_label.setStyleSheet("QLabel { color: red; font-size: 16px; padding: 5px; }")
        top_bar.addWidget(self.connection_label)
        top_bar.addStretch()
        self.device_info_label = QLabel("Serial: —")
        self.device_info_label.setStyleSheet("QLabel { color: #aaaaaa; font-size: 13px; padding: 5px; }")
        top_bar.addWidget(self.device_info_label)
        main_layout.addLayout(top_bar)

        # Tab widget for plots
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Grid layout for displays
        lcd_layout = QGridLayout()
        lcd_layout.setSpacing(15)
        main_layout.addLayout(lcd_layout)

        # Initialize displays and charts
        self.lcd_displays = {}
        self.plot_widgets = {}
        self.plot_lines = {}
        self.connectors = {}

        # Mapping of data points to tab indices, to be populated during tab creation
        self.tab_mapping = {}

        # Create 4 frames for 4 columns
        frames = [QFrame() for _ in range(4)]
        frame_layouts = [QVBoxLayout() for _ in range(4)]
        for i, frame in enumerate(frames):
            frame.setFrameShape(QFrame.Box)
            frame.setFrameShadow(QFrame.Raised)
            frame.setMinimumWidth(200)
            frame_layouts[i].setContentsMargins(5, 5, 5, 5)
            frame_layouts[i].setSpacing(5)
            frame.setLayout(frame_layouts[i])
            lcd_layout.addWidget(frame, 0, i)
            lcd_layout.setColumnStretch(i, 1)

        # Distribute 23 data points across 4 columns (6, 6, 6, 5)
        items_per_column = [6, 6, 6, 5]
        item_index = 0
        for col, num_items in enumerate(items_per_column):
            for _ in range(num_items):
                if item_index >= len(self.all_data_points):
                    logging.error(f"Index {item_index} exceeds all_data_points length")
                    break
                name = self.all_data_points[item_index]
                pair_layout = QHBoxLayout()
                if name in ["solar_port", "usb_port_c1", "usb_port_c2", "usb_port_c3", "usb_port_a1", "dc_port", "light"]:
                    lcd = ClickableLabel()
                    if name in ["usb_c1_power", "usb_c2_power", "usb_c3_power", "light"]:
                        lcd.clicked.connect(lambda n=name: self.switch_to_tab(n))
                    lcd.setStyleSheet("QLabel { background: rgb(85, 87, 83); color: black; padding: 5px; font-size: 14px; }")
                    lcd.setFixedSize(120, 24)
                    lcd.setText("Unknown")
                else:
                    lcd = ClickableLCDNumber()
                    lcd.setDigitCount(4)
                    lcd.setSegmentStyle(QLCDNumber.Flat)
                    lcd.setStyleSheet("QLCDNumber { background: rgb(85, 87, 83); color: black; }")
                    lcd.setFixedSize(120, 24)
                    lcd.display(0)
                    if name in ["ac_power_out", "dc_power_out", "usb_c1_power", "usb_c2_power", "usb_c3_power", "usb_a1_power", "power_out", "ac_power_in", "solar_power_in", "power_in", "battery_percentage"]:
                        lcd.clicked.connect(lambda n=name: self.switch_to_tab(n))
                self.lcd_displays[name] = lcd
                lcd_label = ClickableLabel(name.replace('_', ' ').title())
                lcd_label.setStyleSheet("QLabel { font-weight: bold; color: black; font-size: 14px; }")
                if name in ["ac_power_out", "dc_power_out", "usb_c1_power", "usb_c2_power", "usb_c3_power", "usb_a1_power", "power_out", "ac_power_in", "solar_power_in", "power_in", "battery_percentage", "light"]:
                    lcd_label.clicked.connect(lambda n=name: self.switch_to_tab(n))
                pair_layout.addWidget(lcd_label)
                pair_layout.addWidget(lcd)
                frame_layouts[col].addLayout(pair_layout)
                item_index += 1

        # Crosshair parameters
        kwargs = {
            Crosshair.ENABLED: True,
            Crosshair.LINE_PEN: pg.mkPen(color="purple"),
            Crosshair.TEXT_KWARGS: {"color": "white"}
        }
        pg.setConfigOption('leftButtonPan', True)

        # Define tab order
        tab_order = [
            "ac_power_out", "dc_power_out", "usb_c_power", "usb_a1_power",
            "power_out", "ac_power_in", "solar_power_in", "power_in",
            "battery_percentage", "light"
        ]

        # Colors for numeric plots with alpha (transparency)
        colors = [
            (30, 144, 255, 50),  # dodgerblue with alpha
            (255, 0, 0, 50),     # red with alpha
            (0, 128, 0, 50),     # green with alpha
            (0, 0, 255, 50),     # blue with alpha
            (255, 0, 255, 50),   # magenta with alpha
            (0, 255, 255, 50),   # cyan with alpha
            (255, 255, 0, 50),   # yellow with alpha
            (128, 0, 128, 50)    # purple with alpha
        ]
        color_index = 0

        # Create tabs and populate tab_mapping
        for tab_index, tab_name in enumerate(tab_order):
            if tab_name == "usb_c_power":
                # Combined USB-C Power chart
                usb_c1_plot = LiveLinePlot(pen="red", name="USB-C1 Power", brush=(255, 0, 0))  # red with alpha
                usb_c2_plot = LiveLinePlot(pen="green", name="USB-C2 Power",brush=(0, 128, 0))  # green with alpha
                usb_c3_plot = LiveLinePlot(pen="blue", name="USB-C3 Power", brush=(0, 0, 255))  # blue with alpha
                self.connectors['usb_c1_power'] = DataConnector(usb_c1_plot, max_points=87200, update_rate=1)
                self.connectors['usb_c2_power'] = DataConnector(usb_c2_plot, max_points=87200, update_rate=1)
                self.connectors['usb_c3_power'] = DataConnector(usb_c3_plot, max_points=87200, update_rate=1)
                usb_c_bottom_axis = LiveDateAxis(orientation="bottom", base_timestamp=self.base_timestamp, **{Axis.TICK_FORMAT: Axis.DATETIME})
                self.plot_widgets['usb_c_power'] = LivePlotWidget(
                    title="USB-C Power, 24 Hours",
                    axisItems={'bottom': usb_c_bottom_axis},
                    x_range_controller=LiveAxisRange(roll_on_tick=300, offset_left=.5),
                    **kwargs
                )
                self.plot_widgets['usb_c_power'].x_range_controller.crop_left_offset_to_data = True
                self.plot_widgets['usb_c_power'].showGrid(x=True, y=True, alpha=0.3)
                self.plot_widgets['usb_c_power'].setLabel('left', 'Power (W)')
                self.plot_widgets['usb_c_power'].addLegend(offset=("0","-350"))
                self.plot_widgets['usb_c_power'].addItem(usb_c1_plot)
                self.plot_widgets['usb_c_power'].addItem(usb_c2_plot)
                self.plot_widgets['usb_c_power'].addItem(usb_c3_plot)
                self.tabs.addTab(self.plot_widgets['usb_c_power'], "USB-C Power")
                # Map USB-C related data points to this tab
                self.tab_mapping['usb_c1_power'] = tab_index
                self.tab_mapping['usb_c2_power'] = tab_index
                self.tab_mapping['usb_c3_power'] = tab_index
            elif tab_name == "light":
                # Chart for light status
                categories = ["Unknown", "Off", "Low", "Medium", "High"]
                state_plot = LiveCategorizedBarPlot(
                    categories,
                    category_color={
                        "Unknown": "saddlebrown",
                        "Off": "red",
                        "Low": "darkblue",
                        "Medium": "green",
                        "High": "yellow"
                    }
                )
                self.connectors['light'] = DataConnector(state_plot, max_points=15120, update_rate=0.2)
                state_left_axis = LiveAxis("left", **{Axis.TICK_FORMAT: Axis.CATEGORY, Axis.CATEGORIES: categories})
                state_bottom_axis = LiveDateAxis(orientation="bottom", base_timestamp=self.base_timestamp, **{Axis.TICK_FORMAT: Axis.DATETIME})
                self.plot_widgets['light'] = LivePlotWidget(
                    title="Light Status, 24 Hours",
                    axisItems={'bottom': state_bottom_axis, 'left': state_left_axis},
                    x_range_controller=LiveAxisRange(roll_on_tick=75, offset_left=.5),
                    **kwargs
                )
                self.plot_widgets['light'].x_range_controller.crop_left_offset_to_data = True
                self.plot_widgets['light'].showGrid(x=True, y=True, alpha=0.3)
                self.plot_widgets['light'].setLabel('bottom')
                self.plot_widgets['light'].addItem(state_plot)
                self.tabs.addTab(self.plot_widgets['light'], "Light Status")
                self.tab_mapping['light'] = tab_index
            else:
                # Numeric data points
                plot = LiveLinePlot(
                    pen=colors[color_index % len(colors)],
                    fillLevel=0,
                    brush=colors[color_index % len(colors)],
                    name=tab_name.replace('_', ' ').title()
                )
                self.connectors[tab_name] = DataConnector(plot, max_points=87200, update_rate=1)
                bottom_axis = LiveDateAxis(orientation="bottom", base_timestamp=self.base_timestamp, **{Axis.TICK_FORMAT: Axis.DATETIME})
                self.plot_widgets[tab_name] = LivePlotWidget(
                    title=f"{tab_name.replace('_', ' ').title()}, 24 Hours",
                    axisItems={'bottom': bottom_axis},
                    x_range_controller=LiveAxisRange(roll_on_tick=300, offset_left=.5),
                    **kwargs
                )
                self.plot_widgets[tab_name].x_range_controller.crop_left_offset_to_data = True
                self.plot_widgets[tab_name].showGrid(x=True, y=True, alpha=0.3)
                self.plot_widgets[tab_name].setLabel('left', 'Value')
                self.plot_widgets[tab_name].addLegend(offset=("0","-350"))
                self.plot_widgets[tab_name].addItem(plot)
                self.tabs.addTab(self.plot_widgets[tab_name], tab_name.replace('_', ' ').title())
                self.tab_mapping[tab_name] = tab_index
                color_index += 1

        # Connect to device
        asyncio.run_coroutine_threadsafe(self.connect_to_device(), loop)

        # GUI update timer
        self.timer = QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_gui)
        self.timer.start()

        # Chart update timer
        self.chart_timer = QTimer()
        self.chart_timer.setInterval(200)
        self.chart_timer.timeout.connect(self.update_charts)
        self.chart_timer.start()

    def switch_to_tab(self, name):
        """Switch to the tab corresponding to the given data point name."""
        if name in self.tab_mapping:
            logging.debug(f"Switching to tab for {name}, index {self.tab_mapping[name]}")
            self.tabs.setCurrentIndex(self.tab_mapping[name])

    async def connect_to_device(self):
        self.connection_label.setText("Scanning...")
        self.connection_label.setStyleSheet("QLabel { color: yellow; font-size: 16px; padding: 5px; }")
        devices = await discover_devices(timeout=5)

        if self.device_address:
            # Filter to the requested MAC address
            target_device = next((d for d in devices if d.address == self.device_address), None)
            if not target_device:
                logging.error(f"Device with address {self.device_address} not found!")
                self.connection_label.setText("Disconnected")
                self.connection_label.setStyleSheet("QLabel { color: red; font-size: 16px; padding: 5px; }")
                return
            self.setWindowTitle(f"SolixBLE Data Monitor — {target_device.name} ({target_device.address})")
        else:
            # Auto-discover: use the first found Solix device
            if not devices:
                logging.error("No Solix devices found nearby!")
                self.connection_label.setText("No Device Found")
                self.connection_label.setStyleSheet("QLabel { color: red; font-size: 16px; padding: 5px; }")
                return
            target_device = devices[0]
            logging.debug(f"Auto-discovered device: {target_device.name} ({target_device.address})")
            self.setWindowTitle(f"SolixBLE Data Monitor — {target_device.name} ({target_device.address})")

        # Use C300 for C300/C300X devices. Change to C1000 for C1000 devices.
        self.solix_device = C300(target_device)
        self.solix_device.add_callback(self.update_gui_with_data)

        self.connection_label.setText("Negotiating...")
        self.connection_label.setStyleSheet("QLabel { color: orange; font-size: 16px; padding: 5px; }")

        # connect() handles BLE connection, encryption negotiation, and waits
        # for the first real telemetry packet before returning.
        connected = await self.solix_device.connect()
        if not connected:
            logging.error(f"Connection to {self.device_address} failed")
            self.connection_label.setText("Disconnected")
            self.connection_label.setStyleSheet("QLabel { color: red; font-size: 16px; padding: 5px; }")
            return

        self.connection_label.setText("Connected")
        self.connection_label.setStyleSheet("QLabel { color: green; font-size: 16px; padding: 5px; }")
    
    def _extract_device_info(self):
        """Read serial number from the decrypted telemetry packet and update the label.
        Serial number is at bytes 171-187 (confirmed via the library's serial_number property)."""
        data = self.solix_device._data
        if data is None or len(data) < 188:
            return

        try:
            serial = self.solix_device.serial_number.strip()
        except Exception:
            try:
                serial = data[171:187].decode("ascii").rstrip("\x00").strip()
            except Exception:
                serial = "?"

        self.device_info_label.setText(f"Serial: {serial}")
        self.device_info_label.setToolTip("Serial number from telemetry bytes 171–187")

    def update_gui_with_data(self):
        """Callback invoked by SolixBLE whenever new telemetry arrives.
        Also called periodically from the QTimer to keep the GUI current."""
        if not self.solix_device or not self.solix_device.available:
            self.connection_label.setText("Disconnected")
            self.connection_label.setStyleSheet("QLabel { color: red; font-size: 16px; padding: 5px; }")
            return

        self.connection_label.setText("Connected")
        self.connection_label.setStyleSheet("QLabel { color: green; font-size: 16px; padding: 5px; }")

        try:
            current_time = datetime.now().timestamp()
            if self.base_timestamp is None:
                self.base_timestamp = current_time  # Set base timestamp on first data
                # Update base_timestamp for all LiveDateAxis instances
                for widget in self.plot_widgets.values():
                    bottom_axis = widget.getAxis('bottom')
                    if isinstance(bottom_axis, LiveDateAxis):
                        bottom_axis.base_timestamp = self.base_timestamp

            values = {}
            for name in self.all_data_points:
                value = getattr(self.solix_device, name, None)
                if value is None:
                    continue  # skip attributes not present on this device class
                if isinstance(value, (PortStatus, LightStatus)):
                    value = value.value
                if name in ["ac_power_out", "dc_power_out", "power_out"]:
                    value = -value
                elif name == "usb_c1_power" and self.solix_device.usb_port_c1.value == 1:
                    value = -value
                elif name == "usb_c2_power" and self.solix_device.usb_port_c2.value == 1:
                    value = -value
                elif name == "usb_c3_power" and self.solix_device.usb_port_c3.value == 1:
                    value = -value
                elif name == "usb_a1_power" and self.solix_device.usb_port_a1.value == 1:
                    value = -value
                values[name] = value

            self.time.append(current_time - self.base_timestamp)  # Normalize timestamp
            for name in self.all_data_points:
                if name not in values:
                    continue
                value = values[name]
                if name in self.numeric_data_points:
                    self.data[name].append(value)
                if name == "light":
                    self.lcd_displays[name].setText(self.light_status_map.get(value, "Unknown"))
                elif name == "solar_port":
                    solar_w = values.get("solar_power_in", 0)
                    self.lcd_displays[name].setText("Charging" if solar_w > 0 else "Idle")
                elif name in ["usb_port_c1", "usb_port_c2", "usb_port_c3", "usb_port_a1", "dc_port"]:
                    self.lcd_displays[name].setText(self.port_status_map.get(value, "Unknown"))
                else:
                    self.lcd_displays[name].display(value)

            if not self.first_data_received:
                self.first_data_received = True
                logging.debug("Initial data received")
                if not self.telemetry_logged:
                    self.telemetry_logged = True
                    self._extract_device_info()

        except Exception as e:
            logging.error(f"Error in update_gui_with_data: {e}")

    def update_gui(self):
        self.update_gui_with_data()

    def update_charts(self):
        try:
            if self.first_data_received and self.base_timestamp is not None:
                timestamp = datetime.now().timestamp() - self.base_timestamp  # Normalize timestamp
                for name in self.numeric_data_points:
                    self.connectors[name].cb_append_data_point(float(self.data[name][-1]), timestamp)
                light_status = self.lcd_displays['light'].text()
                light_index = list(self.light_status_map.values()).index(light_status)
                self.connectors['light'].cb_append_data_point([list(self.light_status_map.values())[light_index]], timestamp)
        except (AttributeError, IndexError, ValueError) as e:
            logging.error(f"Error in update_charts: {e}")

    def closeEvent(self, event):
        # Don't accept yet — run async cleanup first so BLE is properly released
        event.ignore()
        self.timer.stop()
        self.chart_timer.stop()

        async def _shutdown():
            if self.solix_device:
                try:
                    await self.solix_device.disconnect()
                except Exception as e:
                    logging.error(f"Error during disconnect on close: {e}")
            loop.stop()  # exits loop.run_forever() → process exits cleanly

        asyncio.ensure_future(_shutdown())

if __name__ == "__main__":
    # Optional: pass a MAC address as a command-line argument to target a
    # specific device. If omitted, the first discovered Solix device is used.
    # Usage: ./Anker-Power-Monitor-Clickable.py [MAC_ADDRESS]
    # Example: ./Anker-Power-Monitor-Clickable.py F4:9D:8A:57:02:82
    mac = sys.argv[1] if len(sys.argv) > 1 else None
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = SolixBLEGUI(mac)
    window.show()
    with loop:
        loop.run_forever()
