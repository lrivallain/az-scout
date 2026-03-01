"""E2E scenario 3: Deployment Planner — select sub + region, load SKUs, verify table."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _select_region(page: Page, region_name: str = "francecentral") -> None:
    """Select a region via the combobox."""
    search = page.get_by_test_id("region-search")
    search.click()
    search.fill(region_name)
    page.locator(f"#region-dropdown li[data-value='{region_name}']").click()


def _select_planner_sub(page: Page, sub_name: str = "Dev") -> None:
    """Select a subscription in the planner dropdown."""
    search = page.get_by_test_id("planner-sub-search")
    search.click()
    search.fill(sub_name)
    # Click the first matching dropdown item
    page.locator("#planner-sub-dropdown li").first.click()


def _navigate_to_planner(page: Page, base_url: str) -> None:
    """Navigate to the Deployment Planner tab."""
    page.goto(base_url)
    # Wait for subs to load
    page.wait_for_function(
        """() => {
            const el = document.getElementById('planner-sub-search');
            return el && !el.disabled;
        }""",
        timeout=10000,
    )
    page.locator("#planner-tab").click()


def test_planner_tab_switch(page: Page, base_url: str) -> None:
    """Clicking the Planner tab shows the planner pane."""
    _navigate_to_planner(page, base_url)
    planner_pane = page.locator("#tab-planner")
    expect(planner_pane).to_be_visible()


def test_planner_empty_state(page: Page, base_url: str) -> None:
    """The planner shows an empty state prompt initially."""
    _navigate_to_planner(page, base_url)
    empty = page.get_by_test_id("planner-empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("Load SKUs")


def test_planner_load_btn_disabled_initially(page: Page, base_url: str) -> None:
    """Load SKUs button is disabled until region + sub are selected."""
    _navigate_to_planner(page, base_url)
    load_btn = page.get_by_test_id("planner-load-btn")
    expect(load_btn).to_be_disabled()


def test_planner_load_skus(page: Page, base_url: str) -> None:
    """Selecting sub + region and loading SKUs shows the results table."""
    _navigate_to_planner(page, base_url)

    _select_region(page)
    _select_planner_sub(page)

    load_btn = page.get_by_test_id("planner-load-btn")
    expect(load_btn).to_be_enabled()
    load_btn.click()

    # Wait for results
    results = page.get_by_test_id("planner-results")
    expect(results).to_be_visible(timeout=15000)

    # SKU table should exist
    sku_table = page.get_by_test_id("sku-table-container")
    expect(sku_table).to_be_visible()
    # Table should have rows (header + data rows)
    sku_table.locator("table").wait_for(state="attached", timeout=5000)


def test_planner_sku_names_in_table(page: Page, base_url: str) -> None:
    """The SKU table contains the fixture SKU names."""
    _navigate_to_planner(page, base_url)

    _select_region(page)
    _select_planner_sub(page)

    page.get_by_test_id("planner-load-btn").click()

    results = page.get_by_test_id("planner-results")
    expect(results).to_be_visible(timeout=15000)

    # Verify at least one fixture SKU name appears
    table = page.get_by_test_id("sku-table-container")
    expect(table).to_contain_text("Standard_D2s_v3")
