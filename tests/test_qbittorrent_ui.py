"""UI tests for the qBittorrent settings card and manual torrent grabs (issue #81).

Template rendering and i18n parity use the real app; settings.js / search.js
run for real in a Node vm with a tiny DOM + fetch stub (no network, no live
client). Secrets used below are fake sentinels.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "app" / "web" / "static" / "js"
SETTINGS_JS = JS_DIR / "settings.js"
SEARCH_JS = JS_DIR / "search.js"
I18N = ROOT / "app" / "web" / "i18n"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

QBT_IDS = (
    "qbt-enabled",
    "qbt-name",
    "qbt-url",
    "qbt-category",
    "qbt-tag",
    "qbt-api-key",
    "qbt-username",
    "qbt-password",
    "qbt-test-btn",
    "qbt-msg",
)

# Generic harness: argv = [js file, i18n json, scenario js]. The scenario runs
# inside the vm and may use `els` (id -> element), `calls` (fetch log),
# `toasts`, `logs`, and `routes` (url -> handler).
NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const T = JSON.parse(process.argv[2]);
const ids = JSON.parse(process.argv[4]);
const els = {};
for (const id of ids) els[id] = { id, value: "", checked: false, placeholder: "", textContent: "", disabled: false, dataset: {}, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {} };
const calls = [], toasts = [], logs = [];
const routes = {};
const sandbox = {
  window: { AUDIARR_I18N: T, AudiarrToast: { success: (m) => toasts.push(["success", m]), error: (m) => toasts.push(["error", m]) } },
  document: { addEventListener() {}, getElementById: (id) => els[id] || null, documentElement: { dataset: {} }, querySelectorAll: () => [] },
  els, calls, toasts, logs, routes, URLSearchParams, JSON, Promise,
  console: { log: (...a) => logs.push(a.join(" ")), debug: (...a) => logs.push(a.join(" ")), info: (...a) => logs.push(a.join(" ")), warn: (...a) => logs.push(a.join(" ")), error: (...a) => logs.push(a.join(" ")) },
  fetch: async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null });
    const r = routes[(opts.method || "GET") + " " + url];
    const h = r || { status: 200, json: {} };
    return { ok: h.status >= 200 && h.status < 300, status: h.status, json: async () => h.json };
  },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: process.argv[1] });
(async () => {
  const out = await vm.runInContext("(async () => {" + process.argv[3] + "})()", sandbox);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run(js_file: Path, scenario: str, ids: tuple[str, ...]):
    strings = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    proc = subprocess.run(
        ["node", "-e", NODE_HARNESS, str(js_file), json.dumps(strings), scenario, json.dumps(list(ids))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _settings_doc(extra_clients):
    return {
        "auth": {},
        "ui": {"theme": "dark"},
        "updates": {},
        "backup": {},
        "media_management": {},
        "conversion": {},
        "indexers": [],
        "host": {},
        "metadata": {},
        "wanted": {},
        "release_preferences": {},
        "metadata_profiles": [],
        "quality_definitions": [],
        "quality_profiles": [],
        "root_folders": [],
        "download_clients": extra_clients,
    }


SAB = {"name": "SABnzbd", "type": "sabnzbd", "url": "http://sab", "category": "books", "enabled": True}
QBT_STORED = {
    "name": "qB",
    "type": "qbittorrent",
    "url": "http://qb:8080",
    "username": "legacy",
    "category": "audiobooks",
    "tag": "audiarr",
    "enabled": True,
    "api_key_set": True,
    "password_set": True,
}

SETTINGS_IDS = QBT_IDS + ("sab-enabled", "sab-name", "sab-url", "sab-category", "sab-api-key", "settings-msg")


# --- rendered page ---------------------------------------------------------


def test_download_clients_page_renders_qbittorrent_card(app_client):
    page = app_client.get("/settings/download-clients")
    assert page.status_code == 200
    for el_id in QBT_IDS:
        assert f'id="{el_id}"' in page.text
    # SAB card is untouched and no save-path / auto-submit / scheduler fields exist.
    assert 'id="sab-url"' in page.text
    for forbidden in ("qbt-save-path", "qbt-savepath", "qbt-auto", "qbt-interval", "qbt-schedule"):
        assert forbidden not in page.text
    assert "qBittorrent ≥ 5.2.0" in page.text
    assert "write-only" in page.text.lower()
    # Secret inputs are password fields and never carry a value attribute.
    for secret_id in ("qbt-api-key", "qbt-password"):
        tag = page.text.split(f'id="{secret_id}"')[1].split(">")[0]
        assert "value=" not in tag
    assert "settings.js" in page.text


# --- settings.js load / save contract -------------------------------------


@needs_node
def test_populate_uses_secret_indicators_and_never_shows_secrets():
    doc = _settings_doc([SAB, QBT_STORED])
    out = _run(
        SETTINGS_JS,
        f"populate({json.dumps(doc)}); return {{ ...Object.fromEntries(Object.entries(els).map(([k, e]) => [k, {{v: e.value, c: e.checked, p: e.placeholder}}])) }};",
        SETTINGS_IDS,
    )
    assert out["qbt-enabled"]["c"] is True
    assert out["qbt-url"]["v"] == "http://qb:8080"
    assert out["qbt-username"]["v"] == "legacy"
    assert out["qbt-category"]["v"] == "audiobooks"
    assert out["qbt-tag"]["v"] == "audiarr"
    assert out["qbt-api-key"] == {"v": "", "c": False, "p": "•••••"}
    assert out["qbt-password"] == {"v": "", "c": False, "p": "•••••"}
    assert out["sab-url"]["v"] == "http://sab"


@needs_node
def test_populate_without_stored_secrets_has_blank_placeholders():
    doc = _settings_doc([{**QBT_STORED, "api_key_set": False, "password_set": False}])
    out = _run(
        SETTINGS_JS,
        f"populate({json.dumps(doc)}); return [els['qbt-api-key'].placeholder, els['qbt-password'].placeholder];",
        SETTINGS_IDS,
    )
    assert out == ["", ""]


def _save(doc, fill: str):
    scenario = f"""
      routes["GET /api/v1/settings"] = {{ status: 200, json: {json.dumps(doc)} }};
      routes["PUT /api/v1/settings"] = {{ status: 200, json: {{}} }};
      populate({json.dumps(doc)});
      {fill}
      await saveSettings({{ preventDefault() {{}} }});
      return {{ put: calls.find((c) => c.method === "PUT"), logs, secretsCleared: [els['qbt-api-key'].value, els['qbt-password'].value] }};
    """
    return _run(SETTINGS_JS, scenario, SETTINGS_IDS)


def _qbt(body):
    return next(c for c in body["download_clients"] if c["type"] == "qbittorrent")


@needs_node
def test_save_blank_secrets_are_omitted_and_sab_is_untouched():
    doc = _settings_doc([SAB, QBT_STORED])
    out = _save(doc, "els['qbt-url'].value = ' http://qb:9090 '; els['qbt-tag'].value = 'mytag';")
    body = out["put"]["body"]
    qbt = _qbt(body)
    assert "api_key" not in qbt and "password" not in qbt
    assert (qbt["url"], qbt["tag"], qbt["category"], qbt["enabled"]) == (
        "http://qb:9090",
        "mytag",
        "audiobooks",
        True,
    )
    assert qbt["username"] == "legacy"
    sab = next(c for c in body["download_clients"] if c["type"] == "sabnzbd")
    assert sab["url"] == "http://sab" and sab["category"] == "books" and sab["enabled"] is True
    assert "api_key" not in sab
    assert not any(k in json.dumps(body) for k in ("save_path", "savepath", "auto_submit"))


@needs_node
def test_save_sends_new_secrets_only_when_typed_and_clears_inputs_without_logging():
    doc = _settings_doc([SAB, QBT_STORED])
    out = _save(doc, "els['qbt-api-key'].value = 'FAKE-KEY-123'; els['qbt-password'].value = 'FAKE-PW-456';")
    qbt = _qbt(out["put"]["body"])
    assert qbt["api_key"] == "FAKE-KEY-123" and qbt["password"] == "FAKE-PW-456"
    assert out["secretsCleared"] == ["", ""]
    assert not any("FAKE-" in line for line in out["logs"])


@needs_node
def test_save_creates_qbittorrent_entry_with_fixed_defaults_when_absent():
    doc = _settings_doc([SAB])
    out = _save(doc, "")
    qbt = _qbt(out["put"]["body"])
    assert (qbt["category"], qbt["tag"], qbt["enabled"]) == ("audiobooks", "audiarr", False)


# --- connection test --------------------------------------------------------


@needs_node
def test_connection_test_posts_only_nonempty_secrets_and_renders_safe_status():
    scenario = """
      routes["POST /api/v1/connections/qbittorrent/test"] = { status: 200, json: { ok: true, message: "qBittorrent v5.2.0" } };
      els['qbt-url'].value = ' http://qb:8080/ ';
      await testQbittorrent();
      const stored = { body: calls[0].body, msg: els['qbt-msg'].textContent };
      els['qbt-api-key'].value = 'FAKE-KEY';
      els['qbt-username'].value = 'u';
      await testQbittorrent();
      return { stored, typed: calls[1].body, url: calls[1].url, toasts, btn: els['qbt-test-btn'].disabled };
    """
    out = _run(SETTINGS_JS, scenario, SETTINGS_IDS)
    assert out["stored"]["body"] == {"url": "http://qb:8080/"}
    assert out["stored"]["msg"] == "✓ qBittorrent v5.2.0"
    assert out["typed"] == {"url": "http://qb:8080/", "api_key": "FAKE-KEY", "username": "u"}
    assert out["url"] == "/api/v1/connections/qbittorrent/test"
    assert out["toasts"][0][0] == "success"
    assert out["btn"] is False


@needs_node
def test_connection_test_failure_and_http_error_are_safe():
    scenario = """
      els['qbt-url'].value = 'http://qb';
      els['qbt-password'].value = 'FAKE-PW';
      routes["POST /api/v1/connections/qbittorrent/test"] = { status: 200, json: { ok: false, message: "Authentication failed" } };
      await testQbittorrent();
      const first = els['qbt-msg'].textContent;
      routes["POST /api/v1/connections/qbittorrent/test"] = { status: 422, json: { detail: [{ msg: "bad" }] } };
      await testQbittorrent();
      return { first, second: els['qbt-msg'].textContent, toasts, logs };
    """
    out = _run(SETTINGS_JS, scenario, SETTINGS_IDS)
    assert out["first"] == "✗ Authentication failed"
    assert out["second"].endswith("(HTTP 422)")
    assert [t[0] for t in out["toasts"]] == ["error", "error"]
    assert "FAKE-PW" not in json.dumps(out)


# --- search.js manual grabs -------------------------------------------------

SEARCH_IDS = ()
TORRENT = {
    "indexer_id": 3,
    "guid": "g-t",
    "title": "Some Torrent",
    "protocol": "torrent",
    "download_url": "http://prowlarr/3/download?apikey=SECRETKEY",
    "magnet_url": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&tr=http://t/PASSKEY",
}
USENET = {
    "indexer_id": 3,
    "guid": "g-u",
    "title": "Some NZB",
    "protocol": "usenet",
    "download_url": "http://prowlarr/3/download?apikey=SECRETKEY",
}


def _grab(row, route):
    scenario = f"""
      lastReleases = [{json.dumps(row)}];
      routes["POST /api/v1/releases/grab"] = {json.dumps(route)};
      const btn = {{ textContent: "Grab", disabled: false }};
      await grabRelease(0, btn);
      return {{ req: calls[0], btn, toasts, logs, html: renderRow({json.dumps(row)}, 0) }};
    """
    return _run(SEARCH_JS, scenario, SEARCH_IDS)


@needs_node
def test_torrent_grab_payload_includes_protocol_and_magnet_and_uses_qbittorrent_wording():
    out = _grab(TORRENT, {"status": 200, "json": {"ok": True, "message": "ok", "torrent_hash": "abc"}})
    assert out["req"]["body"] == {
        "indexer_id": 3,
        "guid": "g-t",
        "title": "Some Torrent",
        "protocol": "torrent",
        "download_url": TORRENT["download_url"],
        "magnet_url": TORRENT["magnet_url"],
    }
    assert out["btn"]["textContent"] == "Sent to qBittorrent"
    assert out["toasts"] == [["success", "Sent to qBittorrent: Some Torrent"]]
    assert "disabled" not in out["html"].split("</button>")[0]
    # Neither the magnet link, its passkey, nor the API key may reach console logs.
    joined = " ".join(out["logs"])
    assert "magnet:" not in joined and "PASSKEY" not in joined and "SECRETKEY" not in joined


@needs_node
def test_usenet_grab_payload_and_wording_stay_sabnzbd():
    out = _grab(USENET, {"status": 200, "json": {"ok": True, "message": "ok", "nzo_id": "SABnzbd_nzo_1"}})
    assert out["req"]["body"]["protocol"] == "usenet"
    assert out["req"]["body"]["magnet_url"] is None
    assert out["req"]["body"]["download_url"] == USENET["download_url"]
    assert out["toasts"] == [["success", "Sent to SABnzbd: Some NZB (SABnzbd_nzo_1)"]]
    assert out["btn"]["textContent"] == "Grabbed"


@needs_node
def test_magnet_only_release_is_grabbable_and_routed_as_torrent():
    row = {**TORRENT, "protocol": None, "download_url": ""}
    out = _grab(row, {"status": 200, "json": {"ok": True, "message": "ok"}})
    assert out["req"]["body"]["magnet_url"] == TORRENT["magnet_url"]
    assert out["toasts"][0][1].startswith("Sent to qBittorrent")


@needs_node
def test_torrent_grab_without_qbittorrent_shows_config_error_and_does_not_fall_back():
    out = _grab(
        TORRENT, {"status": 503, "json": {"detail": "No enabled qBittorrent download client configured"}}
    )
    assert len(out["req"]) and out["toasts"] == [
        [
            "error",
            "Prowlarr and qBittorrent must be configured and enabled before you can grab torrent releases.",
        ]
    ]
    assert out["btn"] == {"textContent": "Grab", "disabled": False}


@needs_node
def test_torrent_grab_failure_toast_is_distinct_and_leaves_button_retryable():
    out = _grab(
        TORRENT, {"status": 200, "json": {"ok": False, "message": "qBittorrent rejected the torrent"}}
    )
    assert out["toasts"] == [["error", "qBittorrent grab failed: qBittorrent rejected the torrent"]]
    assert out["btn"]["disabled"] is False


def test_search_js_has_no_automatic_grab_and_no_url_logging():
    js = SEARCH_JS.read_text(encoding="utf-8")
    assert js.count('fetch("/api/v1/releases/grab"') == 1  # only inside the click-driven grabRelease
    for line in js.splitlines():
        if "console." in line:
            assert "magnet" not in line and "download_url" not in line


# --- i18n -------------------------------------------------------------------


def test_new_qbittorrent_keys_exist_in_both_languages_and_are_used():
    en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    de = json.loads((I18N / "de.json").read_text(encoding="utf-8"))
    keys = {k for k in en if "qbt" in k or k.endswith("_torrent") or k == "search_grab_torrent_title"}
    assert keys and keys == {
        k for k in de if "qbt" in k or k.endswith("_torrent") or k == "search_grab_torrent_title"
    }
    assert set(en) == set(de)
    for lang in (en, de):
        assert "qBittorrent" in lang["search_grab_success_torrent"]
        assert lang["search_grab_success_torrent"] != lang["search_grab_success"]
    assert en["search_grab_success_torrent"] != de["search_grab_success_torrent"]
    template = (ROOT / "app/web/templates/settings/download_clients.html").read_text(encoding="utf-8")
    for key in (k for k in en if k.startswith("settings_dc_qbt_")):
        assert f"t.{key}" in template
