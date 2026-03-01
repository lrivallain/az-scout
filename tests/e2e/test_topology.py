"""E2E scenario 2: AZ Topology — select region + subs, load mappings, verify graph + table."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _select_region(page: Page, region_name: str = "francecentral") -> None:
    """Select a region via the combobox."""
    search = page.get_by_test_id("region-search")
    search.click()
    search.fill(region_name)
    page.locator(f"#region-dropdown li[data-value='{region_name}']").click()


def _wait_for_subs(page: Page) -> None:
    """Wait until subscriptions are rendered in the topology sidebar."""
    page.wait_for_function(
        """() => {
            const el = document.getElementById('topo-sub-list');
            return el && el.querySelectorAll('input[type=checkbox]').length > 0;
        }""",
        timeout=10000,
    )


def test_subscription_list_populated(page: Page, base_url: str) -> None:
    """Subscription checklist shows fixture subscriptions."""
    page.goto(base_url)
    _wait_for_subs(page)

    checkboxes = page.locator("#topo-sub-list input[type=checkbox]")
    expect(checkboxes).to_have_count(2)


def test_load_button_disabled_without_region(page: Page, base_url: str) -> None:
    """Load button stays disabled when no region is selected."""
    page.goto(base_url)
    _wait_for_subs(page)

    load_btn = page.get_by_test_id("topo-load-btn")
    expect(load_btn).to_be_disabled()


def test_load_button_enabled_after_region_and_sub(page: Page, base_url: str) -> None:
    """Load button is enabled once a region and subscription are selected."""
    page.goto(base_url)
    _wait_for_subs(page)

    # Select a region
    _select_region(page)

    # Check a subscription
    page.locator("#topo-sub-list input[type=checkbox]").first.check()

    load_btn = page.get_by_test_id("topo-load-btn")
    expect(load_btn).to_be_enabled()


def test_load_mappings_shows_graph(page: Page, base_url: str) -> None:
    """Loading mappings shows the graph and table containers."""
    page.goto(base_url)
    _wait_for_subs(page)

    _select_region(page)

    # Select all subscriptions
    page.locator("#topo-sub-list input[type=checkbox]").first.check()
    page.locator("#topo-sub-list input[type=checkbox]").nth(1).check()

    # Click Load
    page.get_by_test_id("topo-load-btn").click()

    # Wait for results
    results = page.get_by_test_id("topo-results")
    expect(results).to_be_visible(timeout=10000)

    # Graph container should have SVG content
    graph = page.get_by_test_id("graph-container")
    expect(graph).to_be_visible()
    graph.locator("svg").wait_for(state="attached", timeout=5000)


def test_load_mappings_shows_table(page: Page, base_url: str) -> None:
    """Loading mappings populates the mapping table."""
    page.goto(base_url)
    _wait_for_subs(page)

    _select_region(page)
    page.locator("#topo-sub-list input[type=checkbox]").first.check()
    page.get_by_test_id("topo-load-btn").click()

    # Wait for results
    results = page.get_by_test_id("topo-results")
    expect(results).to_be_visible(timeout=10000)

    # Table container should have a table element
    table_container = page.get_by_test_id("table-container")
    expect(table_container).to_be_visible()
    table_container.locator("table").wait_for(state="attached", timeout=5000)


def test_empty_state_visible_initially(page: Page, base_url: str) -> None:
    """The empty-state prompt is visible before loading data."""
    page.goto(base_url)
    empty = page.get_by_test_id("topo-empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("Load")
