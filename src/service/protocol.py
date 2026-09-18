"""Public wire contract. Importing this module performs no I/O."""

from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, tostring

BUS_NAME = "io.github.iashutoshtiwari.PredatorSense"
OBJECT_PATH = "/io/github/iashutoshtiwari/PredatorSense"
INTERFACE = BUS_NAME + ".Control"
ACTION_ID = "io.github.iashutoshtiwari.predatorsense.control"
ERROR_PREFIX = BUS_NAME + ".Error."

# Scalar signatures keep the contract usable from QtDBus and ordinary bus tools.
# Unknown integer telemetry/control values are -1; temperatures are millidegrees C.
READ_METHODS = {
    "GetHardwareIdentity": ("", "ssbb"),
    "GetStatus": ("", "bss"),
    "GetFanState": ("", "ssii"),
    "GetCoolBoost": ("", "i"),
    "GetTemperatures": ("", "ii"),
    "GetFanSpeeds": ("", "ii"),
}
CONTROL_METHODS = {
    "SetCpuFanMode": ("s", ""),
    "SetGpuFanMode": ("s", ""),
    "SetCpuManualSpeed": ("i", ""),
    "SetGpuManualSpeed": ("i", ""),
    "SetCoolBoost": ("b", ""),
    "SetGlobalAuto": ("", ""),
    "SetGlobalTurbo": ("", ""),
}
METHODS = READ_METHODS | CONTROL_METHODS


@dataclass
class ServiceError(Exception):
    code: str
    message: str

    def __str__(self):
        return self.message


def validate_call(member: str, signature: str, body: list) -> None:
    if member not in METHODS:
        raise ServiceError("UnknownMethod", "Method is not exposed by Predator Sense")
    expected = METHODS[member][0]
    if signature != expected or len(body) != len(expected):
        raise ServiceError("InvalidArgs", "Incorrect method signature")
    for kind, value in zip(expected, body):
        if type(value) is not {"s": str, "i": int, "b": bool}[kind]:
            raise ServiceError("InvalidArgs", "Incorrect argument type")
    if member.endswith("FanMode") and body[0] not in ("auto", "turbo", "manual"):
        raise ServiceError("InvalidArgs", "Mode must be auto, turbo, or manual")
    if member.endswith("ManualSpeed") and not 0 <= body[0] <= 100:
        raise ServiceError("InvalidArgs", "Manual percentage must be in 0..100")


def introspection_xml() -> str:
    root = Element("node", name=OBJECT_PATH)
    interface = SubElement(root, "interface", name=INTERFACE)
    for name, (inputs, outputs) in METHODS.items():
        method = SubElement(interface, "method", name=name)
        for direction, signature in (("in", inputs), ("out", outputs)):
            for index, kind in enumerate(signature):
                SubElement(method, "arg", name=f"{direction}{index}", type=kind, direction=direction)
    intro = SubElement(root, "interface", name="org.freedesktop.DBus.Introspectable")
    method = SubElement(intro, "method", name="Introspect")
    SubElement(method, "arg", name="xml", type="s", direction="out")
    peer = SubElement(root, "interface", name="org.freedesktop.DBus.Peer")
    SubElement(peer, "method", name="Ping")
    return tostring(root, encoding="unicode")
