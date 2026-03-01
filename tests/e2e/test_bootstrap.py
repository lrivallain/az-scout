"""E2E scenario 1: Bootstrap — page loads, tenants and regions populate."""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_page_loads_with_title(page: Page, base_url: str) -> None:
    """The main page loads and has the correct title."""
    page.goto(base_url)
    expect(page).to_have_title("Azure Scout")


def test_navbar_visible(page: Page, base_url: str) -> None:
    """The navbar with the brand name is visible."""
    page.goto(base_url)
    brand = page.locator(".navbar-brand")
    expect(brand).to_be_visible()
    expect(brand).to_contain_text("Azure Scout")


def test_tenant_selector_populated(page: Page, base_url: str) -> None:
    """The tenant selector is populated with fixture tenants."""
    page.goto(base_url)
    tenant_select = page.get_by_test_id("tenant-select")
    # Wait for the tenant dropdown to be populated (no "Loading" text)
    tenant_select.wait_for(state="attached")
    page.wait_for_function(
        """() => {
            const sel = document.getElementById('tenant-select');
            return sel && sel.options.length > 1
                && !sel.options[0].text.includes('Loading');
        }"""
    )
    # Should have the 2 fixture tenants (no empty option when >1 tenant)
    options = tenant_select.locator("option")
    expect(options).to_have_count(2)  # tid-1 + tid-2


def test_region_search_enabled(page: Page, base_url: str) -> None:
    """The region search input becomes enabled after tenants load."""
    page.goto(base_url)
    region_search = page.get_by_test_id("region-search")
    # Wait for regions to load (input becomes enabled)
    page.wait_for_function(
        """() => {
            const el = document.getElementById('region-search');
            return el && !el.disabled;
        }""",
        timeout=10000,
    )
    expect(region_search).to_be_enabled()


def test_two_tabs_visible(page: Page, base_url: str) -> None:
    """The two built-in tabs are visible."""
    page.goto(base_url)
    tabs = page.locator("#mainTabs .nav-link")
    # At least 2 built-in tabs; plugins may add more
    assert tabs.count() >= 2
    expect(tabs.nth(0)).to_contain_text("AZ Topology")
    expect(tabs.nth(1)).to_contain_text("Deployment Planner")


def test_topology_tab_active_by_default(page: Page, base_url: str) -> None:
    """The AZ Topology tab is active on page load."""
    page.goto(base_url)
    topo_tab = page.locator("#topology-tab")
    expect(topo_tab).to_have_class(re.compile("active"))


def test_theme_toggle(page: Page, base_url: str) -> None:
    """The theme toggle switches between light and dark mode."""
    page.goto(base_url)
    html = page.locator("html")
    expect(html).to_have_attribute("data-bs-theme", "light")

    # Click theme toggle
    page.locator("#theme-toggle").click()
    expect(html).to_have_attribute("data-bs-theme", "dark")

    # Click again to toggle back
    page.locator("#theme-toggle").click()
    expect(html).to_have_attribute("data-bs-theme", "light")
