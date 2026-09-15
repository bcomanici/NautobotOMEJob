"""Nautobot Job: synchronize Dell OpenManage Enterprise bare-metal inventory."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import requests
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify
from nautobot.apps.jobs import (
    BooleanVar,
    ChoiceVar,
    DryRunVar,
    IntegerVar,
    Job,
    ObjectVar,
    StringVar,
)
from nautobot.dcim.models import Device, DeviceType, Location, Manufacturer, Platform, SoftwareVersion
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import CustomField, Role, SecretsGroup, Status

from .ome_helpers import SystemRecord, find_match, parse_system

name = "Dell OpenManage Enterprise"

CUSTOM_FIELDS = (
    {
        "key": "cpu",
        "label": "CPU",
        "type": "text",
        "description": "CPU model/details reported by Dell OpenManage Enterprise.",
    },
    {
        "key": "installed_ram",
        "label": "Installed RAM",
        "type": "text",
        "description": "Installed memory reported by Dell OpenManage Enterprise.",
    },
    {
        "key": "system_of_record",
        "label": "System of Record",
        "type": "text",
        "description": "Authoritative inventory source for this object.",
    },
    {
        "key": "last_synced_from_sor",
        "label": "Last sync from System of Record",
        "type": "date",
        "description": "Date this object was last synchronized from its system of record.",
    },
)

MATCH_CHOICES = (
    ("hostname_or_serial", "Hostname or serial number"),
    ("hostname", "Hostname only"),
    ("serial", "Serial number only"),
)


class SyncOpenManageEnterpriseDevices(Job):
    """Create or update Nautobot Devices from Dell OME inventory."""

    ome_url = StringVar(
        label="OpenManage Enterprise URL",
        description="Base URL of the OME appliance, for example https://openmanage.example.org.",
    )
    secrets_group = ObjectVar(
        model=SecretsGroup,
        description="Secrets Group with the OME username and password under Generic/HTTP access.",
    )
    verify_tls = BooleanVar(
        default=True,
        description="Verify the OME appliance TLS certificate.",
    )
    default_location = ObjectVar(
        model=Location,
        description="Location assigned to newly created Devices.",
    )
    device_role = ObjectVar(
        model=Role,
        query_params={"content_types": "dcim.device"},
        description="Role assigned to newly created Devices.",
    )
    device_status = ObjectVar(
        model=Status,
        query_params={"content_types": "dcim.device"},
        description="Status assigned to created and updated Devices.",
    )
    match_mode = ChoiceVar(
        choices=MATCH_CHOICES,
        default="hostname_or_serial",
        description="Fields used to find an existing bare-metal Device and prevent duplicate ingestion.",
    )
    update_existing_devices = BooleanVar(
        default=False,
        description="Update matched Devices. When disabled, matched Devices are skipped.",
    )
    create_missing_device_types = BooleanVar(
        default=True,
        description="Create missing Manufacturer and Device Type records from OME inventory.",
    )
    create_missing_platforms = BooleanVar(
        default=True,
        description="Create missing Platform and Software Version records from the running OS.",
    )
    page_size = IntegerVar(
        default=100,
        min_value=1,
        max_value=1000,
        description="Number of OME Devices requested per API page.",
    )
    dryrun = DryRunVar()

    class Meta:
        name = "Sync OpenManage Enterprise Devices"
        description = "Synchronize bare-metal inventory from Dell OpenManage Enterprise into Nautobot."
        has_sensitive_variables = False
        field_order = [
            "ome_url", "secrets_group", "verify_tls", "default_location", "device_role",
            "device_status", "match_mode", "update_existing_devices", "create_missing_device_types",
            "create_missing_platforms", "page_size", "dryrun",
        ]

    def run(
        self,
        *,
        ome_url: str,
        secrets_group: SecretsGroup,
        verify_tls: bool,
        default_location: Location,
        device_role: Role,
        device_status: Status,
        match_mode: str,
        update_existing_devices: bool,
        create_missing_device_types: bool,
        create_missing_platforms: bool,
        page_size: int,
        dryrun: bool,
    ) -> str:
        dryrun = bool(dryrun)
        if dryrun:
            self.logger.info("DRY RUN enabled: Nautobot objects will not be created or updated.")

        self._ensure_custom_fields(dryrun=dryrun)
        username = self._get_secret(secrets_group, "username")
        password = self._get_secret(secrets_group, "password")
        records = self._fetch_ome_systems(
            base_url=ome_url,
            username=username,
            password=password,
            verify_tls=bool(verify_tls),
            page_size=int(page_size),
        )
        self.logger.info("Fetched %s device records from OpenManage Enterprise.", len(records))

        devices = list(Device.objects.all())
        counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        for system in records:
            try:
                if not system.hostname:
                    raise RuntimeError(f"OME device {system.ome_id!r} has no usable hostname")
                match = find_match(devices, system, match_mode)
                if match is not None and not update_existing_devices:
                    self.logger.info(
                        "Skipping matched Device %s (serial %s, Nautobot ID %s).",
                        system.hostname,
                        system.serial,
                        match.pk,
                    )
                    counts["skipped"] += 1
                    continue

                manufacturer, device_type = self._get_or_create_device_type(
                    manufacturer_name=system.manufacturer,
                    model_name=system.model,
                    create_missing=create_missing_device_types,
                    dryrun=dryrun,
                )
                if (
                    device_type is None
                    and match is None
                    and not (dryrun and create_missing_device_types)
                ):
                    raise RuntimeError(
                        f"Device Type {system.manufacturer}/{system.model} is missing and creation is disabled"
                    )

                platform = self._get_or_create_platform(
                    platform_name=system.platform_name,
                    create_missing=create_missing_platforms,
                    dryrun=dryrun,
                )
                software_version = self._get_or_create_software_version(
                    version=system.software_version,
                    platform=platform,
                    create_missing=create_missing_platforms,
                    dryrun=dryrun,
                )

                if dryrun:
                    self.logger.info(
                        "DRY RUN: Would %s Device %s: manufacturer=%s model=%s serial=%s "
                        "platform=%s software_version=%s cpu=%s installed_ram=%s.",
                        "update" if match else "create",
                        system.hostname,
                        manufacturer.name if manufacturer else system.manufacturer,
                        device_type.model if device_type else system.model,
                        system.serial,
                        platform.name if platform else system.platform_name,
                        software_version.version if software_version else system.software_version,
                        system.cpu,
                        system.installed_ram,
                    )
                    counts["updated" if match else "created"] += 1
                    continue

                if match is None:
                    device = Device(
                        name=system.hostname,
                        serial=system.serial,
                        device_type=device_type,
                        location=default_location,
                        role=device_role,
                        status=device_status,
                    )
                else:
                    device = match
                    device.name = system.hostname
                    device.serial = system.serial
                    device.status = device_status
                    if device_type is not None:
                        device.device_type = device_type

                if platform is not None:
                    device.platform = platform
                if software_version is not None and self._model_has_field(Device, "software_version"):
                    device.software_version = software_version
                device.cf["cpu"] = system.cpu
                device.cf["installed_ram"] = system.installed_ram
                device.cf["system_of_record"] = "OpenManage Enterprise"
                device.cf["last_synced_from_sor"] = date.today().isoformat()
                device.validated_save()
                if match is None:
                    devices.append(device)
                counts["updated" if match else "created"] += 1
            except Exception as exc:  # Process independent OME records even if one fails.
                counts["failed"] += 1
                self.logger.error(
                    "Failed OME device %s (%s): %s",
                    system.hostname or "<no hostname>",
                    system.ome_id,
                    exc,
                )

        summary = (
            f"OME sync complete: created={counts['created']}, updated={counts['updated']}, "
            f"skipped={counts['skipped']}, failed={counts['failed']}"
        )
        self.logger.info(summary)
        if counts["failed"]:
            raise RuntimeError(summary)
        return summary

    def _fetch_ome_systems(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool,
        page_size: int,
    ) -> List[SystemRecord]:
        base_url = base_url.rstrip("/")
        session = requests.Session()
        session.verify = verify_tls
        session.headers.update({"Accept": "application/json"})
        response = session.post(
            f"{base_url}/api/SessionService/Sessions",
            json={"UserName": username, "Password": password, "SessionType": "API"},
            timeout=60,
        )
        self._raise_for_status(response, "creating OME API session")
        token = response.headers.get("X-Auth-Token")
        if not token:
            raise RuntimeError("OME did not return an X-Auth-Token")
        session.headers["X-Auth-Token"] = token

        records: List[SystemRecord] = []
        skip = 0
        while True:
            response = session.get(
                f"{base_url}/api/DeviceService/Devices",
                params={"$top": page_size, "$skip": skip},
                timeout=60,
            )
            self._raise_for_status(response, "listing OME devices")
            payload = response.json()
            rows = payload.get("value", payload if isinstance(payload, list) else [])
            for row in rows:
                ome_id = row.get("Id", row.get("DeviceId"))
                device_data = dict(row)
                inventory: Any = {}
                if ome_id not in (None, ""):
                    detail_response = session.get(
                        f"{base_url}/api/DeviceService/Devices({ome_id})",
                        timeout=60,
                    )
                    if detail_response.ok:
                        detail_payload = detail_response.json()
                        if isinstance(detail_payload, dict):
                            device_data.update({
                                key: value
                                for key, value in detail_payload.items()
                                if value not in (None, "", [])
                            })
                    else:
                        self.logger.warning(
                            "Could not fetch the detailed OME device record for %s (%s): %s",
                            ome_id,
                            detail_response.status_code,
                            detail_response.text[:500],
                        )
                    inv_response = session.get(
                        f"{base_url}/api/DeviceService/Devices({ome_id})/InventoryDetails",
                        timeout=60,
                    )
                    if inv_response.ok:
                        inventory = inv_response.json()
                    else:
                        self.logger.warning(
                            "Could not fetch detailed inventory for OME device %s (%s). "
                            "The Job will use fields from the device summary: %s",
                            ome_id,
                            inv_response.status_code,
                            inv_response.text[:500],
                        )
                system = parse_system(device_data, inventory)
                if system.platform_name or system.software_version:
                    self.logger.info(
                        "OME running OS for %s: platform=%s software_version=%s.",
                        system.hostname or ome_id,
                        system.platform_name or "<blank>",
                        system.software_version or "<blank>",
                    )
                else:
                    self.logger.warning(
                        "OME returned no running OS name/version for %s after summary, detail, and inventory queries.",
                        system.hostname or ome_id,
                    )
                records.append(system)
            if len(rows) < page_size:
                break
            skip += len(rows)
        return records

    @staticmethod
    def _raise_for_status(response: requests.Response, action: str) -> None:
        if not response.ok:
            raise RuntimeError(
                f"OME request failed while {action} ({response.status_code}): {response.text[:1000]}"
            )

    def _ensure_custom_fields(self, *, dryrun: bool) -> None:
        device_content_type = ContentType.objects.get_for_model(Device)
        for spec in CUSTOM_FIELDS:
            custom_field = (
                CustomField.objects.filter(key=spec["key"]).first()
                or CustomField.objects.filter(label__iexact=spec["label"]).first()
            )
            if custom_field is None:
                if dryrun:
                    self.logger.info(
                        "DRY RUN: Would create custom field %s (%s).", spec["label"], spec["key"]
                    )
                    continue
                kwargs = {
                    "type": spec["type"],
                    "key": spec["key"],
                    "label": spec["label"],
                    "required": False,
                }
                if self._model_has_field(CustomField, "description"):
                    kwargs["description"] = spec["description"]
                custom_field = CustomField(**kwargs)
                custom_field.validated_save()
                self.logger.info("Created custom field %s (%s).", spec["label"], spec["key"])

            if hasattr(custom_field, "content_types") and device_content_type not in custom_field.content_types.all():
                if dryrun:
                    self.logger.info(
                        "DRY RUN: Would assign custom field %s to Device objects.", spec["key"]
                    )
                else:
                    custom_field.content_types.add(device_content_type)

    def _get_or_create_device_type(
        self,
        *,
        manufacturer_name: str,
        model_name: str,
        create_missing: bool,
        dryrun: bool,
    ) -> tuple[Optional[Manufacturer], Optional[DeviceType]]:
        manufacturer = Manufacturer.objects.filter(name__iexact=manufacturer_name).first()
        if manufacturer is None and create_missing and not dryrun:
            kwargs: Dict[str, Any] = {"name": manufacturer_name}
            if self._model_has_field(Manufacturer, "slug"):
                kwargs["slug"] = self._unique_slug(Manufacturer, manufacturer_name)
            manufacturer = Manufacturer(**kwargs)
            manufacturer.validated_save()
        elif manufacturer is None and create_missing:
            self.logger.info("DRY RUN: Would create Manufacturer %s.", manufacturer_name)

        device_type = None
        if manufacturer is not None:
            device_type = DeviceType.objects.filter(
                manufacturer=manufacturer, model__iexact=model_name
            ).first()
        if device_type is None and create_missing and not dryrun:
            if manufacturer is None:
                raise RuntimeError(f"Could not resolve Manufacturer {manufacturer_name}")
            kwargs = {"manufacturer": manufacturer, "model": model_name}
            if self._model_has_field(DeviceType, "slug"):
                kwargs["slug"] = self._unique_slug(DeviceType, f"{manufacturer_name}-{model_name}")
            device_type = DeviceType(**kwargs)
            device_type.validated_save()
        elif device_type is None and create_missing:
            self.logger.info("DRY RUN: Would create Device Type %s/%s.", manufacturer_name, model_name)
        return manufacturer, device_type

    def _get_or_create_platform(
        self, *, platform_name: str, create_missing: bool, dryrun: bool
    ) -> Optional[Platform]:
        platform_name = str(platform_name or "").strip()
        if not platform_name:
            return None
        platform = Platform.objects.filter(name__iexact=platform_name).first()
        if platform is not None:
            if self._model_has_field(Platform, "manufacturer") and platform.manufacturer is not None:
                if dryrun:
                    self.logger.info("DRY RUN: Would clear manufacturer on Platform %s.", platform.name)
                else:
                    platform.manufacturer = None
                    platform.validated_save()
            return platform
        if not create_missing:
            return None
        if dryrun:
            self.logger.info("DRY RUN: Would create Platform %s.", platform_name)
            return None
        kwargs: Dict[str, Any] = {"name": platform_name}
        if self._model_has_field(Platform, "manufacturer"):
            kwargs["manufacturer"] = None
        if self._model_has_field(Platform, "slug"):
            kwargs["slug"] = self._unique_slug(Platform, platform_name)
        platform = Platform(**kwargs)
        platform.validated_save()
        return platform

    def _get_or_create_software_version(
        self,
        *,
        version: str,
        platform: Optional[Platform],
        create_missing: bool,
        dryrun: bool,
    ) -> Optional[SoftwareVersion]:
        version = str(version or "").strip()
        if not version:
            return None
        query = SoftwareVersion.objects.filter(version__iexact=version)
        if platform is not None and self._model_has_field(SoftwareVersion, "platform"):
            query = query.filter(platform=platform)
        software_version = query.first()
        if software_version is not None:
            return software_version
        if not create_missing:
            return None
        if dryrun:
            self.logger.info(
                "DRY RUN: Would create Software Version %s%s.",
                version,
                f" for Platform {platform.name}" if platform else "",
            )
            return None
        kwargs: Dict[str, Any] = {"version": version}
        if platform is not None and self._model_has_field(SoftwareVersion, "platform"):
            kwargs["platform"] = platform
        if self._model_has_field(SoftwareVersion, "status"):
            status = self._get_status_for_model(SoftwareVersion)
            if status is None:
                raise RuntimeError(
                    "No Status is assigned to Software Version. Create an Active or Current status for that content type."
                )
            kwargs["status"] = status
        software_version = SoftwareVersion(**kwargs)
        software_version.validated_save()
        return software_version

    @staticmethod
    def _get_status_for_model(model: Any) -> Optional[Status]:
        content_type = ContentType.objects.get_for_model(model)
        queryset = Status.objects.filter(content_types=content_type)
        return (
            queryset.filter(name__iexact="Active").first()
            or queryset.filter(name__iexact="Current").first()
            or queryset.first()
        )

    def _get_secret(self, secrets_group: SecretsGroup, kind: str) -> str:
        access_type = self._choice_value(
            SecretsGroupAccessTypeChoices,
            ("TYPE_GENERIC", "TYPE_HTTP", "TYPE_REST"),
            "generic",
        )
        names = ("TYPE_USERNAME",) if kind == "username" else ("TYPE_PASSWORD", "TYPE_TOKEN")
        fallback = "username" if kind == "username" else "password"
        secret_type = self._choice_value(SecretsGroupSecretTypeChoices, names, fallback)
        try:
            value = secrets_group.get_secret_value(access_type=access_type, secret_type=secret_type)
        except Exception as exc:
            raise RuntimeError(f"Could not retrieve OME {kind} from Secrets Group: {exc}") from exc
        if not value:
            raise RuntimeError(f"OME {kind} in Secrets Group is empty")
        return str(value).strip()

    @staticmethod
    def _choice_value(choice_class: Any, names: Iterable[str], fallback: str) -> str:
        for name in names:
            if hasattr(choice_class, name):
                return getattr(choice_class, name)
        return fallback

    @staticmethod
    def _model_has_field(model: Any, field_name: str) -> bool:
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    @staticmethod
    def _unique_slug(model: Any, value: str) -> str:
        base = slugify(value)[:45] or "item"
        candidate = base
        suffix = 2
        while model.objects.filter(slug=candidate).exists():
            candidate = f"{base[:40]}-{suffix}"
            suffix += 1
        return candidate
