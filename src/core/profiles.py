"""The single supported G3-572 hardware contract; no model selection machinery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from core.errors import HardwareError

SUPPORTED_PRODUCT = "Predator G3-572"
TESTED_BIOS = "V1.22"
EC_IO_FILE = "/sys/kernel/debug/ec/ec0/io"
EC_MODULE_PATH = "/sys/module/ec_sys"
DEBUGFS_PATH = "/sys/kernel/debug"

COOLBOOST_REGISTER = 0x10
COOLBOOST_OFF = 0x00
COOLBOOST_ON = 0x01
CPU_FAN_MODE_REGISTER = 0x22
GPU_FAN_MODE_REGISTER = 0x21
CPU_FAN_CONTROL_REGISTER = 0x37
GPU_FAN_CONTROL_REGISTER = 0x3A
CPU_FAN_RPM_REGISTER = 0x13
GPU_FAN_RPM_REGISTER = 0x15
FAN_RPM_MAX = 6122


class FanChannel(Enum):
    CPU = "cpu"
    GPU = "gpu"


class FanMode(Enum):
    FIRMWARE_AUTO = "firmware_auto"
    AUTO = "auto"
    MANUAL = "manual"
    TURBO = "turbo"
    UNKNOWN = "unknown"


MODE_REGISTERS = MappingProxyType({FanChannel.CPU: CPU_FAN_MODE_REGISTER, FanChannel.GPU: GPU_FAN_MODE_REGISTER})
CONTROL_REGISTERS = MappingProxyType(
    {FanChannel.CPU: CPU_FAN_CONTROL_REGISTER, FanChannel.GPU: GPU_FAN_CONTROL_REGISTER}
)
RPM_REGISTERS = MappingProxyType({FanChannel.CPU: CPU_FAN_RPM_REGISTER, FanChannel.GPU: GPU_FAN_RPM_REGISTER})
MODE_VALUES = MappingProxyType(
    {
        FanChannel.CPU: MappingProxyType(
            {
                FanMode.FIRMWARE_AUTO: 0x00,
                FanMode.AUTO: 0x54,
                FanMode.TURBO: 0x58,
                FanMode.MANUAL: 0x5C,
            }
        ),
        FanChannel.GPU: MappingProxyType(
            {
                FanMode.FIRMWARE_AUTO: 0x00,
                FanMode.AUTO: 0x50,
                FanMode.TURBO: 0x60,
                FanMode.MANUAL: 0x70,
            }
        ),
    }
)
# Firmware Auto is observable, but only explicit Auto is a verified write operation.
WRITABLE_VALUES = MappingProxyType(
    {
        COOLBOOST_REGISTER: frozenset((COOLBOOST_OFF, COOLBOOST_ON)),
        **{
            MODE_REGISTERS[channel]: frozenset(values[mode] for mode in (FanMode.AUTO, FanMode.TURBO, FanMode.MANUAL))
            for channel, values in MODE_VALUES.items()
        },
        CPU_FAN_CONTROL_REGISTER: frozenset(range(101)),
        GPU_FAN_CONTROL_REGISTER: frozenset(range(101)),
    }
)
BYTE_READ_REGISTERS = frozenset(WRITABLE_VALUES)
WORD_READ_REGISTERS = frozenset(RPM_REGISTERS.values())


@dataclass(frozen=True)
class HardwareIdentity:
    product_name: str
    bios_version: str | None

    @property
    def supported(self) -> bool:
        return self.product_name == SUPPORTED_PRODUCT

    @property
    def tested_bios(self) -> bool:
        return self.bios_version == TESTED_BIOS


@dataclass(frozen=True)
class HardwareStatus:
    identity: HardwareIdentity | None
    readable: bool
    writable: bool
    error: HardwareError | None = None
