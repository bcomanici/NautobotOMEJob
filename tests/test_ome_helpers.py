import unittest

from jobs.ome_helpers import SystemRecord, find_match, format_memory, parse_system


class DeviceStub:
    def __init__(self, pk, name, serial):
        self.pk = pk
        self.name = name
        self.serial = serial


class OmeHelperTests(unittest.TestCase):
    def record(self, hostname="srv01.example.org", serial="ABC-123"):
        return SystemRecord(1, hostname, "Dell Inc.", "PowerEdge R650", serial,
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


if __name__ == "__main__":
    unittest.main()

