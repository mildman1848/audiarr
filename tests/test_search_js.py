"""Browser-independent coverage for the Releases page quality-fit filter
(issue #22): the toggle hides rows whose quality_status is not
`preferred`/`accepted`, defaults OFF, and remembers its state in
localStorage under a stable key.

search.js is plain (non-module) browser JS with no test harness of its own,
so the pure filtering/storage helpers (`isQualityFit`, `qualityStatusOf`,
`loadQualityFitOnly`, `saveQualityFitOnly`) are exercised for real by
loading the actual file into a Node vm context with minimal `document`/
`localStorage` stubs — no fake DOM/rendering is needed since those helpers
never touch the DOM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SEARCH_JS = Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "js" / "search.js"

NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(process.argv[1], "utf8");

const store = {};
const sandbox = {
  window: {},
  document: { addEventListener: () => {} },
  console,
  localStorage: {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "search.js" });

const cases = [
  { quality_status: "preferred" },
  { quality_status: "accepted" },
  { quality_status: "below_cutoff" },
  { quality_status: "rejected" },
  { quality_status: "unknown" },
  {},
  { title: "no quality fields at all" },
];

const filterResults = cases.map((r) => ({
  status: sandbox.qualityStatusOf(r),
  fit: sandbox.isQualityFit(r),
}));

const storageResults = {};
storageResults.defaultValue = sandbox.loadQualityFitOnly();
sandbox.saveQualityFitOnly(true);
storageResults.afterSaveTrue = sandbox.loadQualityFitOnly();
sandbox.saveQualityFitOnly(false);
storageResults.afterSaveFalse = sandbox.loadQualityFitOnly();

console.log(JSON.stringify({ filterResults, storageResults }));
"""


@pytest.fixture(scope="module")
def harness_result():
    if shutil.which("node") is None:
        pytest.skip("node is not available in this environment")

    proc = subprocess.run(
        ["node", "-e", NODE_HARNESS, "--", str(SEARCH_JS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_quality_fit_covers_all_five_statuses(harness_result):
    expected = {
        "preferred": True,
        "accepted": True,
        "below_cutoff": False,
        "rejected": False,
        "unknown": False,
    }
    by_status = {row["status"]: row["fit"] for row in harness_result["filterResults"][:5]}
    assert by_status == expected


def test_missing_quality_fields_are_treated_as_unknown_and_hidden(harness_result):
    # Cases 6 and 7 (index 5, 6) omit quality_status entirely.
    for row in harness_result["filterResults"][5:]:
        assert row["status"] == "unknown"
        assert row["fit"] is False


def test_quality_fit_toggle_defaults_off_and_persists(harness_result):
    storage = harness_result["storageResults"]
    assert storage["defaultValue"] is False
    assert storage["afterSaveTrue"] is True
    assert storage["afterSaveFalse"] is False


def test_quality_fit_storage_key_is_stable_and_namespaced():
    """The localStorage key must not silently change name/shape across
    releases (issue #22 asks for a "stable, specific" key)."""
    js = SEARCH_JS.read_text(encoding="utf-8")
    assert 'const QUALITY_FIT_STORAGE_KEY = "audiarr.releaseSearch.onlyQualityFit";' in js


def test_quality_fit_toggle_wires_change_to_persist_and_rerender():
    js = SEARCH_JS.read_text(encoding="utf-8")
    assert 'getElementById("rs-quality-fit-only")' in js
    assert "saveQualityFitOnly(qualityFitToggle.checked)" in js
    assert "qualityFitToggle.checked = loadQualityFitOnly()" in js


def test_hidden_row_count_is_rendered_when_rows_are_filtered():
    js = SEARCH_JS.read_text(encoding="utf-8")
    assert 'id="rs-quality-fit-hidden-count"' in js
    assert "T.search_quality_fit_hidden_count" in js
    assert "hiddenCount > 0" in js


def test_grab_buttons_use_original_index_so_grab_still_targets_correct_row():
    """Filtering must not shift data-index off from lastReleases — grabRelease
    looks rows up by index into the unfiltered array."""
    js = SEARCH_JS.read_text(encoding="utf-8")
    assert "const indexed = lastReleases.map((r, i) => ({ r, i }));" in js
    assert "visible.map(({ r, i }) => renderRow(r, i))" in js
