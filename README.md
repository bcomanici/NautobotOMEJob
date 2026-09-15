# Dell OpenManage Enterprise Nautobot Job

This repository is ready to add to Nautobot as a Git data source with Jobs enabled. The Job synchronizes Dell OpenManage Enterprise (OME) bare-metal inventory into Nautobot Device records.

## Repository layout

```text
__init__.py
jobs/
  __init__.py
  ome_helpers.py
  openmanage_enterprise.py
tests/
  test_ome_helpers.py
requirements.txt
README.md
```

`jobs/__init__.py` imports the Job class and calls `register_jobs()`. This is important for Git data sources because Nautobot imports the repository's `<slug>.jobs` package during synchronization; it does not automatically import every child module.

The top-level `__init__.py` is also required. It makes the repository itself importable as the Python package named from the Nautobot Git repository slug, such as `dellomesync`, before Nautobot imports `dellomesync.jobs`.

## Data mapping

| OME value | Nautobot destination |
| --- | --- |
| Device name / hostname | Device `name` |
| Management IP when hostname is absent or IP-only | Device `name` fallback: `<make> <model> <management IP>` |
| Service tag | Device `serial` |
| System vendor / make | Manufacturer |
| Model | Device Type |
| Running OS name | Platform |
| Running OS version | Software Version and Device `software_version`, when supported |
| Processor inventory | Custom field `cpu` |
| Installed memory | Custom field `installed_ram` |
| `OpenManage Enterprise` | Custom field `system_of_record` |
| Job run date | Custom field `last_synced_from_sor` |

The Platform and Software Version behavior follows the supplied RMM Job: both are reused case-insensitively, created when allowed, and assigned through native Device fields. Platforms represent software and therefore have no hardware Manufacturer association. If Software Version has a Status field, the Job selects `Active`, then `Current`, then the first valid status for that content type.

## Duplicate protection

The default match mode checks hostname and serial number. Hostnames are case-insensitive and compare both FQDN and short-name forms. Serial numbers ignore punctuation and case. Existing matches are skipped unless **Update existing Devices** is enabled. If the hostname and serial resolve to different Devices, that OME record fails without changing either Device.

For an appliance whose OME `DeviceName` is only its management IP, the Job creates a readable deterministic name such as `Dell Inc. ME5212 172.24.2.39`. Matching also checks the raw management IP, allowing the Job to recognize a pre-existing Device named only `172.24.2.39`.

## Custom fields

The Job checks and, when not in dry-run mode, creates or assigns these existing canonical fields to `dcim.device`:

- `cpu`
- `installed_ram`
- `system_of_record`
- `last_synced_from_sor`

This makes installation order independent.

## Add the repository to Nautobot

1. Commit this repository to a Git server reachable by Nautobot workers.
2. In Nautobot, create the credentials needed to read that repository if it is private.
3. Add a Git repository or Git-backed data source in **Extensibility > Data Sources**. Use the repository clone URL and branch used for production.
4. Enable **Jobs** as provided content and synchronize the data source.
5. Enable the **Sync OpenManage Enterprise Devices** Job if your Nautobot configuration requires explicit Job approval/enabling.

Nautobot web and worker containers must both receive the synchronized repository content. If `requests` is absent from your image, add the pinned dependency from `requirements.txt` to the Nautobot environment and restart the relevant services.

## OME credentials

Create a Nautobot Secrets Group with:

- Generic/HTTP username: the OME API username
- Generic/HTTP password: the OME API password

The Job creates an OME API session and reads:

- `/api/DeviceService/Devices`
- `/api/DeviceService/Devices(<id>)`
- `/api/DeviceService/Devices(<id>)/InventoryDetails`

The per-device detail request is intentional: some OME versions expose the running OS on the detailed Device resource even when it is absent from the paginated Device summary. OS extraction is case- and separator-insensitive across common OME keys. Each run logs the detected Platform and Software Version, or an explicit warning when OME returns neither value.

Use an OME account with read-only inventory permissions where possible.

## First run

Select the OME URL, Secrets Group, default Location, Device Role, and Device Status. Keep these safe defaults for the first execution:

- Match mode: `Hostname or serial number`
- Update existing Devices: disabled
- Dry run: enabled

Review ambiguous matches, missing Device Types, and proposed Platform/Software Version values. Then run without dry-run when the preview is correct.

## Test outside Nautobot

The pure parsing and matching helpers do not import Django or Nautobot:

```bash
python -m unittest discover -s tests -v
```

Full Job integration testing still requires a Nautobot development environment because the Job uses Nautobot ORM models, content types, Secrets Groups, and Job variables.

## Compatibility note

The Job targets the modern `nautobot.apps.jobs` and `CustomField.key` interfaces reflected by the supplied RMM Job. OME inventory response keys vary by OME release. The helper recognizes common device, processor, memory, operating-system, and version keys; add a fixture to `tests/test_ome_helpers.py` if your appliance returns a different shape.

## References

- Nautobot Jobs: https://docs.nautobot.com/projects/core/en/stable/user-guide/platform-functionality/jobs/
- Nautobot Git repositories/data sources: https://docs.nautobot.com/projects/core/en/stable/user-guide/platform-functionality/gitrepository/
- Dell OpenManage Enterprise REST API Guide: https://www.dell.com/support/manuals/en-us/dell-openmanage-enterprise/ome_p_api_guide/
