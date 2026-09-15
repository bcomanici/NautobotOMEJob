"""Jobs supplied by this Git data source.

Nautobot imports this package (``<repository_slug>.jobs``) when synchronizing a
Git repository, so Job registration must occur here rather than only in a
child module.
"""

from nautobot.apps.jobs import register_jobs

from .openmanage_enterprise import SyncOpenManageEnterpriseDevices

jobs = [SyncOpenManageEnterpriseDevices]
register_jobs(*jobs)
