"""UI tests for the reading-list import section on Settings > Import Lists (issue #79).

Page/template asset checks, JS syntax, i18n coverage/parity, and the pure JS
helpers (`apiErrorText`, `rowActions`) run for real in a Node vm. No network.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "web" / "static" / "js" / "import_lists.js"
TEMPLATE = ROOT / "app" / "web" / "templates" / "settings" / "import_lists.html"
I18N = ROOT / "app" / "web" / "i18n"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const T = JSON.parse(process.argv[2]);
const sandbox = { window: { AUDIARR_I18N: T }, document: { addEventListener: () => {} }, console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const out = vm.runInContext(process.argv[3], sandbox);
process.stdout.write(JSON.stringify(out));
"""


def _run(expr: str):
    strings = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    proc = subprocess.run(
        ["node", "-e", NODE_HARNESS, str(JS), json.dumps(strings), expr],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(proc.stdout)


def test_page_renders_reading_list_sections(app_client):
    page = app_client.get("/settings/import-lists")
    assert page.status_code == 200
    for marker in (
        "import-lists-table",
        "reading-list-add-form",
        "reading-list-csv-form",
        "reading-list-preview",
        "rl-import",
        "rl-next-page",
        "rl-next-window",
    ):
        assert marker in page.text
    assert "settings-save-bar" not in page.text
    assert "/static/js/import_lists.js" in page.text


def test_csv_form_offers_only_goodreads_and_storygraph():
    html = TEMPLATE.read_text(encoding="utf-8")
    select = re.search(r'id="rl-csv-format">(.*?)</select>', html, re.S).group(1)
    assert set(re.findall(r'value="([^"]+)"', select)) == {"auto", "goodreads", "storygraph"}
    assert 'accept=".csv,text/csv"' in html


def test_feed_url_input_is_not_prefilled_or_autocompleted():
    html = TEMPLATE.read_text(encoding="utf-8")
    field = re.search(r'<input[^>]*id="rl-feed-url"[^>]*>', html).group(0)
    assert "value=" not in field
    assert 'autocomplete="off"' in field


@needs_node
def test_import_lists_js_syntax():
    subprocess.run(["node", "--check", str(JS)], check=True, capture_output=True, timeout=30)


def test_js_never_renders_or_sends_feed_url_after_submit():
    js = JS.read_text(encoding="utf-8")
    assert js.count("feed_url") == 1  # only the add-form request body
    assert 'urlInput.value = ""' in js
    # Only the explicit import endpoints create books; preview never does.
    assert "/csv/import" in js and "/sources/${encodeURIComponent(rl.origin.id)}/import" in js
    assert "/goodreads/preview" not in js  # no ad-hoc URL preview from the UI


def test_js_import_submits_only_selected_fields():
    js = JS.read_text(encoding="utf-8")
    block = re.search(r"rl\.selected\.set\(key, \{(.*?)\}\);", js, re.S).group(1)
    assert set(re.findall(r"(\w+):", block)) == {"entry_key", "provider_name", "provider_uid", "locale"}
    # Skip is the checked default; no candidate radio is pre-checked.
    assert 'value="" checked' in js
    assert re.search(r'value="\$\{ci\}"[^>]*checked', js) is None


def _js_i18n_keys() -> set[str]:
    js = JS.read_text(encoding="utf-8")
    keys = set(re.findall(r"\bT\.([a-z0-9_]+)", js))
    return {k for k in keys if k.startswith(("reading_list_", "import_lists_"))}


def test_all_referenced_i18n_keys_exist_in_both_languages():
    html_keys = set(re.findall(r"\bt\.([a-z0-9_]+)", TEMPLATE.read_text(encoding="utf-8")))
    referenced = _js_i18n_keys() | html_keys
    for lang in ("en", "de"):
        strings = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in referenced if k not in strings)
        assert not missing, f"{lang}.json missing {missing}"


def test_dynamic_i18n_families_have_parity_and_cover_backend_vocabulary():
    en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    de = json.loads((I18N / "de.json").read_text(encoding="utf-8"))
    rl_en = {k for k in en if k.startswith("reading_list_")}
    assert rl_en == {k for k in de if k.startswith("reading_list_")}
    for key in (
        "reading_list_note_partial_observation",
        "reading_list_note_absence_never_removes",
        "reading_list_note_feed_may_have_more_pages",
        "reading_list_note_preview_window_truncated",
        "reading_list_note_input_row_cap_reached",
        "reading_list_item_created",
        "reading_list_item_skipped_existing",
        "reading_list_item_skipped_unverifiable",
        "reading_list_err_source_disabled",
        "reading_list_err_selection_required",
        "reading_list_err_feed_http_error",
    ):
        assert en[key] and de[key] and en[key] != de[key]


@needs_node
def test_api_error_text_never_echoes_unsafe_detail():
    # Pydantic-style list detail (may echo input such as a feed URL) -> generic text.
    leaked = "https://www.goodreads.com/review/list_rss/1?key=SECRET&shelf=x"
    text = _run(f"apiErrorText({{detail: [{{input: {json.dumps(leaked)}}}]}}, 422)")
    assert "SECRET" not in text and "goodreads" not in text
    assert text == _run("T.reading_list_err_generic")
    # Known code -> translated string; unknown code -> backend's fixed message.
    assert _run('apiErrorText({detail: {code: "feed_url_host", message: "x"}}, 422)') == _run(
        "T.reading_list_err_feed_url_host"
    )
    assert _run('apiErrorText({detail: {code: "brand_new", message: "Safe text."}}, 422)') == "Safe text."


@needs_node
def test_reading_list_rows_use_preview_not_sync():
    rl = _run('rowActions({id: "goodreads-abc", type: "goodreads_rss", enabled: true})')
    assert "data-preview-source" in rl and "data-sync-source" not in rl
    assert "data-toggle-source" in rl and "data-delete-source" in rl
    off = _run('rowActions({id: "goodreads-abc", type: "goodreads_rss", enabled: false})')
    assert re.search(r"data-preview-source=\"[^\"]*\" disabled", off)
    lib = _run('rowActions({id: "liberatarr", type: "liberatarr", enabled: true})')
    assert "data-sync-source" in lib and "data-preview-source" not in lib
