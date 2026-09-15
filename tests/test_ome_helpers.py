import importlib.util
import pathlib
import sys
import unittest

HELPER_PATH = pathlib.Path(__file__).parents[1] / "jobs" / "ome_helpers.py"
SPEC = importlib.util.spec_from_file_location("ome_helpers_under_test", HELPER_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

SystemRecord = MODULE.SystemRecord
find_match = MODULE.find_match
format_memory = MODULE.format_memory
parse_system = MODULE.parse_system
extract_os = MODULE.extract_os


class DeviceStub:
    def __init__(self, pk, name, serial):
        self.pk = pk
        self.name = name
        self.serial = serial


class OmeHelperTests(unittest.TestCase):
    def record(self, hostname="srv01.example.org", serial="ABC-123"):
        return SystemRecord(1, hostname, "", "Dell Inc.", "PowerEdge R650", serial,
                            "Microsoft Windows Server", "2022", "CPU", "64 GiB")

    def test_fqdn_matches_short_hostname(self):
        device = DeviceStub("1", "SRV01", "OTHER")
        self.assertIs(find_match([device], self.record(), "hostname_or_serial"), device)

    def test_normalized_serial_matches(self):
        device = DeviceStub("2", "different", "abc123")
        self.assertIs(find_match([device], self.record(), "serial"), device)

    def test_empty_values_do_not_match(self):
        self.assertIsNone(find_match([DeviceStub("3", "", "")], self.record("", ""), "hostname_or_serial"))

    def test_conflicting_hostname_and_serial_is_ambiguous(self):
        devices = [DeviceStub("1", "srv01", "OTHER"), DeviceStub("2", "other", "ABC123")]
        with self.assertRaises(RuntimeError):
            find_match(devices, self.record(), "hostname_or_serial")

    def test_parse_platform_and_software_version(self):
        device = {
            "Id": 7, "DeviceName": "srv7", "SystemVendor": "Dell Inc.", "Model": "PowerEdge R750",
            "DeviceServiceTag": "TAG7", "OperatingSystem": "Microsoft Windows Server", "MemorySize": 131072,
        }
        inventory = {"value": [
            {"InventoryType": "deviceProcessor", "InventoryInfo": [{"ModelName": "Intel Xeon Gold", "Count": 2}]},
            {"InventoryType": "deviceOperatingSystem", "InventoryInfo": [{"OSVersion": "2022 Datacenter"}]},
        ]}
        record = parse_system(device, inventory)
        self.assertEqual(record.platform_name, "Microsoft Windows Server")
        self.assertEqual(record.software_version, "2022 Datacenter")
        self.assertEqual(record.cpu, "2 x Intel Xeon Gold")
        self.assertEqual(record.installed_ram, "128 GiB")

    def test_byte_memory_conversion(self):
        self.assertEqual(format_memory(137438953472, "bytes"), "128 GiB")

    def test_os_keys_are_case_and_separator_insensitive(self):
        platform, version = extract_os(
            {},
            {"value": [{"inventoryType": "deviceOSInformation", "inventoryInfo": [{
                "OS_Name": "VMware ESXi", "Operating-System-Version": "8.0.3"
            }]}]},
        )
        self.assertEqual(platform, "VMware ESXi")
        self.assertEqual(version, "8.0.3")

    def test_os_from_detailed_device_record(self):
        record = parse_system(
            {
                "Id": 9,
                "DeviceName": "esx9.example.org",
                "OperatingSystemName": "VMware ESXi",
                "OperatingSystemVersion": "8.0 Update 3",
            },
            {},
        )
        self.assertEqual(record.platform_name, "VMware ESXi")
        self.assertEqual(record.software_version, "8.0 Update 3")

    def test_ip_only_device_name_uses_make_model_ip(self):
        record = parse_system(
            {
                "Id": 8,
                "DeviceName": "172.24.2.39",
                "SystemVendor": "Dell Inc.",
                "Model": "ME5212",
                "DeviceServiceTag": "GXHXFH4",
            },
            {},
        )
        self.assertEqual(record.management_ip, "172.24.2.39")
        self.assertEqual(record.hostname, "Dell Inc. ME5212 172.24.2.39")

    def test_generated_name_matches_existing_ip_only_name(self):
        system = SystemRecord(
            8,
            "Dell Inc. ME5212 172.24.2.39",
            "172.24.2.39",
            "Dell Inc.",
            "ME5212",
            "",
            "",
            "",
            "",
            "",
        )
        device = DeviceStub("8", "172.24.2.39", "")
        self.assertIs(find_match([device], system, "hostname"), device)


if __name__ == "__main__":
    unittest.main()
