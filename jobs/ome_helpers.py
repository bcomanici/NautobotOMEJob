"""Pure parsing and matching helpers for the OpenManage Enterprise Job."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


def first(mapping: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return default


def walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def normalize_hostname(value: Optional[str]) -> str:
    return str(value or "").strip().rstrip(".").casefold()


def hostname_candidates(value: Optional[str]) -> set[str]:
    normalized = normalize_hostname(value)
    if not normalized:
        return set()
    return {normalized, normalized.split(".", 1)[0]}


def normalize_serial(value: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def format_memory(value: Any, unit_hint: str = "") -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if amount <= 0:
        return ""

    unit = unit_hint.strip().casefold()
    if unit in {"gb", "gib"}:
        gib = amount
    elif unit in {"kb", "kib"}:
        gib = amount / (1024**2)
    elif unit in {"b", "byte", "bytes"} or amount > 16_000_000:
        gib = amount / (1024**3)
    else:  # OME Device.MemorySize is commonly MiB.
        gib = amount / 1024
    return f"{gib:g} GiB"


@dataclass(frozen=True)
class SystemRecord:
    ome_id: Any
    hostname: str
    manufacturer: str
    model: str
    serial: str
    platform_name: str
    software_version: str
    cpu: str
    installed_ram: str


def _inventory_sections(inventory: Any, wanted: str) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for node in walk_dicts(inventory):
        section_type = str(first(node, "InventoryType", "inventoryType", "Type", default="")).casefold()
        if wanted in section_type:
            info = first(node, "InventoryInfo", "inventoryInfo", "value", default=[])
            if isinstance(info, list):
                matches.extend(item for item in info if isinstance(item, dict))
            elif isinstance(info, dict):
                matches.append(info)
    return matches


def parse_system(device: Dict[str, Any], inventory: Any) -> SystemRecord:
    nodes = list(walk_dicts(inventory))
    processor_rows = _inventory_sections(inventory, "processor")
    if not processor_rows:
        processor_rows = [
            node for node in nodes
            if any(key in node for key in ("ProcessorModel", "ProcessorBrand", "ProcessorName"))
        ]

    cpu_names: List[str] = []
    for row in processor_rows:
        cpu_name = str(first(
            row, "ModelName", "ProcessorModel", "ProcessorBrand", "ProcessorName", "Name"
        )).strip()
        if not cpu_name or cpu_name in cpu_names:
            continue
        count = first(row, "Count", "NumberOfProcessors", "ProcessorCount", default="")
        cpu_names.append(f"{count} x {cpu_name}" if str(count).isdigit() and int(count) > 1 else cpu_name)

    os_rows = _inventory_sections(inventory, "operatingsystem") or _inventory_sections(inventory, "operating system")
    os_row = os_rows[0] if os_rows else {}
    platform_name = str(first(
        device, "OperatingSystem", "OperatingSystemName", "OSName",
        default=first(os_row, "OperatingSystem", "OperatingSystemName", "OSName", "Name"),
    )).strip()
    software_version = str(first(
        device, "OperatingSystemVersion", "OSVersion", "Version",
        default=first(os_row, "OperatingSystemVersion", "OSVersion", "Version"),
    )).strip()

    memory_value = first(device, "MemorySize", "MemorySizeBytes", default=0)
    memory_unit = "bytes" if "MemorySizeBytes" in device and device.get("MemorySizeBytes") else ""
    if not memory_value:
        memory_rows = _inventory_sections(inventory, "memory")
        candidates = []
        for row in memory_rows or nodes:
            value = first(row, "TotalInstalledMemory", "TotalSystemMemoryGiB", "Size", default=0)
            try:
                candidates.append((float(value), str(first(row, "Unit", "SizeUnit", default=""))))
            except (TypeError, ValueError):
                continue
        if candidates:
            memory_value, memory_unit = max(candidates, key=lambda item: item[0])

    return SystemRecord(
        ome_id=first(device, "Id", "DeviceId"),
        hostname=str(first(device, "DeviceName", "HostName", "Identifier")).strip(),
        manufacturer=str(first(device, "SystemVendor", "Manufacturer", default="Dell Inc.")).strip() or "Dell Inc.",
        model=str(first(device, "Model", "ModelName", "DeviceModel", default="Unknown Model")).strip() or "Unknown Model",
        serial=str(first(device, "DeviceServiceTag", "ServiceTag", "SerialNumber")).strip(),
        platform_name=platform_name,
        software_version=software_version,
        cpu="; ".join(cpu_names) or str(first(device, "ProcessorModel", default="")).strip(),
        installed_ram=format_memory(memory_value, memory_unit),
    )


def find_match(devices: Sequence[Any], system: SystemRecord, mode: str) -> Optional[Any]:
    incoming_hosts = hostname_candidates(system.hostname)
    incoming_serial = normalize_serial(system.serial)
    hostname_matches = []
    serial_matches = []
    for device in devices:
        if incoming_hosts and incoming_hosts.intersection(hostname_candidates(getattr(device, "name", ""))):
            hostname_matches.append(device)
        if incoming_serial and normalize_serial(getattr(device, "serial", "")) == incoming_serial:
            serial_matches.append(device)

    if mode == "hostname":
        matches = hostname_matches
    elif mode == "serial":
        matches = serial_matches
    else:
        matches_by_pk = {str(getattr(item, "pk", id(item))): item for item in hostname_matches + serial_matches}
        matches = list(matches_by_pk.values())

    if len(matches) > 1:
        identifiers = [str(getattr(item, "pk", item)) for item in matches]
        raise RuntimeError(
            f"Hostname and/or serial matched multiple Nautobot devices for "
            f"{system.hostname!r}/{system.serial!r}: {identifiers}"
        )
    return matches[0] if matches else None

