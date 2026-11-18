"""The CLI is an API client; run it against the in-process app."""
import pytest
from conftest import login
from typer.testing import CliRunner

from assurance.cli import client as cli

runner = CliRunner()


@pytest.fixture
def cli_api(client, monkeypatch):
    h = login(client, "admin")
    api = cli.Api(url="http://testserver", token=h["Authorization"].split()[1])
    client.headers.update(api.client.headers)
    client.base_url = "http://testserver/api/v1"
    api.client = client
    monkeypatch.setattr(cli, "_api", api)
    return api


def run(*args):
    return runner.invoke(cli.app, list(args))


def test_cli_end_to_end(cli_api, tmp_path):
    from conftest import INVENTORY_CSV

    f = tmp_path / "devices.csv"
    f.write_text(INVENTORY_CSV)
    r = run("devices", "import", str(f))
    assert r.exit_code == 0 and "2 created" in r.output and "Dry run" in r.output
    assert run("devices", "import", str(f), "--apply").exit_code == 0
    r = run("devices", "list")
    assert "yyc-core-rtr01" in r.output and "yyc-dc-nxs01" in r.output

    r = run("snapshot", "--mock-scenario", "t1", "--label", "change:CHG1")
    assert r.exit_code == 0, r.output
    r = run("snapshot", "--mock-scenario", "t2", "--label", "change:CHG1")
    assert r.exit_code == 0 and "change(s) since previous snapshot" in r.output

    r = run("snapshots", "list", "--label", "change:CHG1")
    assert r.output.count("complete") == 2
    r = run("diff")
    assert r.exit_code == 0 and "GigabitEthernet3  oper_status: up -> down" in r.output
    assert run("diff", "--fail-on-change").exit_code == cli.EXIT_CHANGES
    assert run("diff", "latest", "latest", "--fail-on-change").exit_code == 0
    r = run("snapshots", "table", "routes", "--device", "yyc-core-rtr01")
    assert "default|172.16.0.0/16" in r.output
    r = run("devices", "history", "yyc-core-rtr01", "--since", "all")
    assert "GigabitEthernet3" in r.output


def test_cli_reports_api_errors(cli_api):
    r = run("snapshots", "show", "nope")
    assert r.exit_code == 1 and "error 404" in r.output


def test_cli_beta_commands(two_snapshots, cli_api, tmp_path):
    r = run("prepost", "--pre", two_snapshots[0], "--post", two_snapshots[1])
    assert r.exit_code == cli.EXIT_CHECKS, r.output
    assert "Verification FAIL" in r.output and "ospf-neighbor-lost" in r.output
    assert run("prepost", "--pre", two_snapshots[1], "--post", two_snapshots[1]).exit_code == 0
    r = run("checks", "rules")
    assert r.exit_code == 0 and "bgp-established" in r.output
    r = run("checks", "show", "latest", "--fail-on-violation")
    assert r.exit_code == cli.EXIT_CHECKS and "link-mtu-mismatch" in r.output
    r = run("config", "diff", "yyc-dc-nxs01")
    assert r.exit_code == 0 and "+vlan 300" in r.output
    r = run("config", "show", "yyc-core-rtr01")
    assert "<removed>" in r.output
    out = tmp_path / "c.xlsx"
    r = run("report", "changes", "--from", two_snapshots[0], "--format", "xlsx", "-o", str(out))
    assert r.exit_code == 0 and out.read_bytes()[:2] == b"PK"
