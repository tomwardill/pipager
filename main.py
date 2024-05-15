from enum import Enum
import time

import click
import lgpio
import spidev
import structlog

logger = structlog.get_logger()


class BoardRegisters(Enum):
    REG_01_OP_MODE = 0x01


class Board:

    def _handle_interrupt(self, chip, gpio_pin, gpio_level, timestamp):
        self.log.info(
            "Interupt detected",
            chip=chip,
            gpio_pin=gpio_pin,
            gpio_level=gpio_level,
            timestamp=timestamp,
        )

    def spi_read(self, register: int, length: int):
        if length == 1:
            d = self.spi.xfer([register] + [0] * length)[1]
            return d
        else:
            d = self.spi.xfer([register] + [0] * length)[1:]
            return d

    def spi_write(self, register: int, payload: bytes | int):
        if type(payload) == int:
            payload = [payload]
        self.spi.xfer([register | 0x80] + payload)

    def __init__(self, spi_channel: int, interrupt_pin: int, reset_pin: int):
        self.spi_channel = spi_channel
        self.interrupt_pin = interrupt_pin
        self.reset_pin = reset_pin

        self.log = logger.bind(
            spi_channel=self.spi_channel,
            interrupt_pin=self.interrupt_pin,
            reset_pin=self.reset_pin,
        )
        self.log.info("Configuring board")

        self.GPIO_handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(
            self.GPIO_handle, self.interrupt_pin, lgpio.SET_PULL_DOWN
        )
        self.alert = lgpio.gpio_claim_alert(
            self.GPIO_handle, self.interrupt_pin, lgpio.RISING_EDGE
        )
        self.callback = lgpio.callback(
            self.GPIO_handle,
            self.interrupt_pin,
            edge=lgpio.RISING_EDGE,
            func=self._handle_interrupt,
        )

        # reset the board
        if reset_pin:
            self.log.info("Resetting board")
            lgpio.gpio_claim_output(self.GPIO_handle, reset_pin)
            lgpio.gpio_write(self.GPIO_handle, reset_pin, lgpio.LOW)
            time.sleep(0.01)
            lgpio.gpio_write(self.GPIO_handle, reset_pin, lgpio.HIGH)
            time.sleep(0.01)
            self.log.info("Reset complete")

        self.spi = spidev.SpiDev()
        self.spi.open(0, self.spi_channel)
        self.spi.max_speed_hz = 5000000

        board_version = self.spi_read(0x42, 1)
        self.log.info("Board version", board_version=board_version)

        if board_version != 0x12:
            # HOPE RFM98W, SX1278 chip
            self.log.error("Invalid board version")
            return

        # Sleep + FSK mode + FSK modulation = 00000000 (p87)
        self.spi_write(BoardRegisters.REG_01_OP_MODE.value, 0x00)
        time.sleep(0.1)

        # The board defaults to LongRange (LoRa) mode, at 0x80
        # Lets check we actually wrote the change to FSK
        op_mode = self.spi_read(BoardRegisters.REG_01_OP_MODE.value, 1)
        if op_mode != 0x00:
            self.log.error("Failed to set op mode", op_mode=op_mode)
            return

        # Set standby mode
        self.spi_write(BoardRegisters.REG_01_OP_MODE.value, 0x01)

        # Set modem config
        # https://github.com/AaronJackson/rfm69-pocsag/blob/main/rfm69-pocsag/rfm69-pocsag.ino#L43
        self.spi_write(0x02, 0x68)  # RegBitrateMsb
        self.spi_write(0x03, 0x2B)  # RegBitrateLsb
        self.spi_write(0x04, 0x00)  # RegFdevMsb
        self.spi_write(0x05, 0x4A)  # RegFdevLsb

        self.log.info("Modem configured")

        # 0 preamble length as we'll encode that ourselves
        self.spi_write(0x25, 0x00)
        self.spi_write(0x26, 0x00)

        # Set frequency
        # 439.9875 is a licensed frequency (at least in the UK)
        # You must hold an amateur radio license to transmit on this frequency
        fstep = 32000000.0 / 524288
        frf = int((439.9875 * 1000000) / fstep)
        self.spi_write(0x06, (frf >> 16) & 0xFF)
        self.spi_write(0x07, (frf >> 8) & 0xFF)
        self.spi_write(0x08, frf & 0xFF)

        self.log.info("Frequency set")


@click.command()
@click.option("--spi-channel", default=1, help="SPI Channel for the LoRa chip")
@click.option("--interrupt-pin", default=16, help="Interupt pin for the LoRa chip")
@click.option("--reset-pin", default=12, help="Reset pin for the LoRa chip")
def run(spi_channel, interrupt_pin, reset_pin):
    board = Board(spi_channel, interrupt_pin, reset_pin)


if __name__ == "__main__":
    logger.info("Starting")
    run()
