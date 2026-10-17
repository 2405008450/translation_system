from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))


@unittest.skipUnless(BASH and Path(BASH).is_file(), "需要 Bash")
class DeployProxyTests(unittest.TestCase):
    def run_script(self, previous="proxy", requested=None, command="ps", legacy=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            script = root / "scripts" / "deploy_prod.sh"
            script.write_bytes((ROOT / "scripts" / "deploy_prod.sh").read_bytes().replace(b"\r\n", b"\n"))
            (root / ".env.prod").touch()
            env = os.environ.copy()
            env.pop("USE_PROXY", None)
            env.pop("ENV_FILE", None)
            env.update(MOCK_PREVIOUS=previous, MOCK_LEGACY=str(int(legacy)), USE_NGINX="1")
            if requested is not None:
                env["USE_PROXY"] = requested
            # 使用导出的 Bash 函数截获所有 Docker 操作，测试不会访问 Docker 或部署服务。
            wrapper = r'''
docker() {
  if [[ "$1" == "inspect" ]]; then
    case "$MOCK_PREVIOUS" in
      proxy) echo '/project/docker-compose.prod.yml,/project/docker-compose.proxy.yml' ;;
      direct) echo '/project/docker-compose.prod.yml' ;;
      missing) return 1 ;;
    esac
  elif [[ "$1" == "compose" && "$2" == "version" ]]; then
    [[ "$MOCK_LEGACY" == "0" ]]
  else
    printf 'MOCK_DOCKER %s\n' "$*"
  fi
}
docker-compose() { printf 'MOCK_DOCKER_COMPOSE %s\n' "$*"; }
export -f docker docker-compose
bash "$1" "$2"
'''
            return subprocess.run([BASH, "-c", wrapper, "test-wrapper", script.as_posix(), command],
                                  env=env, text=True, encoding="utf-8", capture_output=True)

    def assert_proxy(self, result, enabled):
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [line for line in result.stdout.splitlines() if line.startswith("MOCK_")]
        self.assertTrue(calls)
        self.assertEqual("docker-compose.proxy.yml" in calls[-1], enabled)

    def test_default_retains_existing_proxy(self):
        self.assert_proxy(self.run_script(), True)

    def test_default_keeps_direct_deployment(self):
        self.assert_proxy(self.run_script(previous="direct"), False)

    def test_first_deployment_defaults_to_direct(self):
        self.assert_proxy(self.run_script(previous="missing"), False)

    def test_explicit_disable_overrides_existing_proxy(self):
        self.assert_proxy(self.run_script(requested="0"), False)

    def test_explicit_enable_overrides_direct_deployment(self):
        self.assert_proxy(self.run_script(previous="direct", requested="1"), True)

    def test_invalid_flag_stops_before_deployment(self):
        result = self.run_script(requested="invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("MOCK_", result.stdout)

    def test_restart_starts_proxy_before_recreating_application(self):
        result = self.run_script(command="restart")
        self.assert_proxy(result, True)
        self.assertLess(result.stdout.index("up -d --no-deps mihomo"),
                        result.stdout.index("up -d --force-recreate --no-deps app"))

    def test_legacy_compose_retains_proxy(self):
        result = self.run_script(legacy=True)
        self.assert_proxy(result, True)
        self.assertIn("MOCK_DOCKER_COMPOSE", result.stdout)
