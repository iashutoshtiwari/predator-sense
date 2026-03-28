from __future__ import annotations

import enum
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    gpu_fan_mode_control: int
    gpu_auto_mode: int
    gpu_turbo_mode: int
    gpu_manual_mode: int
    gpu_manual_speed_control: int
    cool_boost_control: int
    cool_boost_on: int
    cool_boost_off: int
    cpu_fan_mode_control: int
    cpu_auto_mode: int
    cpu_turbo_mode: int
    cpu_manual_mode: int
    cpu_manual_speed_control: int
    cpu_auto_values: tuple[int, ...]
    cpu_turbo_values: tuple[int, ...]
    cpu_manual_values: tuple[int, ...]
    gpu_auto_values: tuple[int, ...]
    gpu_turbo_values: tuple[int, ...]
    gpu_manual_values: tuple[int, ...]


class PFS(enum.Enum):
    Manual = 0
    Auto = 1
    Turbo = 2


G3_572_PROFILE = ModelProfile(
    gpu_fan_mode_control=0x21,
    gpu_auto_mode=0x50,
    gpu_turbo_mode=0x60,
    gpu_manual_mode=0x70,
    gpu_manual_speed_control=0x3A,
    cool_boost_control=0x10,
    cool_boost_on=0x01,
    cool_boost_off=0x00,
    cpu_fan_mode_control=0x22,
    cpu_auto_mode=0x54,
    cpu_turbo_mode=0x58,
    cpu_manual_mode=0x5C,
    cpu_manual_speed_control=0x37,
    cpu_auto_values=(84, 0),
    cpu_turbo_values=(88,),
    cpu_manual_values=(92, 93),
    gpu_auto_values=(80, 0),
    gpu_turbo_values=(96,),
    gpu_manual_values=(112,),
)
