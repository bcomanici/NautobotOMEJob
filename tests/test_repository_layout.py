import ast
import pathlib
import unittest


class RepositoryLayoutTests(unittest.TestCase):
    def test_repository_root_is_python_package(self):
        path = pathlib.Path(__file__).parents[1] / "__init__.py"
        self.assertTrue(path.is_file())

    def test_jobs_package_registers_imported_job(self):
        path = pathlib.Path(__file__).parents[1] / "jobs" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_job = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "openmanage_enterprise"
            and any(alias.name == "SyncOpenManageEnterpriseDevices" for alias in node.names)
            for node in tree.body
        )
        registered = any(
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "register_jobs"
            for node in tree.body
        )
        self.assertTrue(imported_job)
        self.assertTrue(registered)


if __name__ == "__main__":
    unittest.main()
