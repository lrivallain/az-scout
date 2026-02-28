"""E2E scenario 4: Strategy Advisor — fill form, submit, verify results."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _navigate_to_strategy(page: Page, base_url: str) -> None:
    """Navigate to the Strategy Advisor tab and wait for subscriptions."""
    page.goto(base_url)
    page.wait_for_function(
        """() => {
            const el = document.getElementById('region-search');
            return el && !el.disabled;
        }""",
        timeout=10000,
    )
    page.locator("#strategy-tab").click()


def _select_strat_sub(page: Page, sub_name: str = "Dev") -> None:
    """Select a subscription in the strategy dropdown."""
    search = page.get_by_test_id("strat-sub-search")
    search.click()
    search.fill(sub_name)
    page.locator("#strat-sub-dropdown li").first.click()


def test_strategy_tab_switch(page: Page, base_url: str) -> None:
    """Clicking the Strategy tab shows the strategy pane."""
    _navigate_to_strategy(page, base_url)
    pane = page.locator("#tab-strategy")
    expect(pane).to_be_visible()


def test_strategy_form_visible(page: Page, base_url: str) -> None:
    """The strategy form with key inputs is visible."""
    _navigate_to_strategy(page, base_url)

    expect(page.get_by_test_id("strat-workload-name")).to_be_visible()
    expect(page.get_by_test_id("strat-sub-search")).to_be_visible()
    expect(page.get_by_test_id("strat-sku")).to_be_visible()
    expect(page.get_by_test_id("strat-instances")).to_be_visible()
    expect(page.get_by_test_id("strat-submit-btn")).to_be_visible()


def test_strategy_submit_shows_results(page: Page, base_url: str) -> None:
    """Submitting the strategy form shows business and technical views."""
    _navigate_to_strategy(page, base_url)

    # Fill form
    page.get_by_test_id("strat-workload-name").fill("e2e-test-workload")
    _select_strat_sub(page)
    page.get_by_test_id("strat-sku").fill("Standard_D2s_v3")
    page.get_by_test_id("strat-instances").fill("2")

    # Submit
    page.get_by_test_id("strat-submit-btn").click()

    # Wait for results
    results = page.get_by_test_id("strategy-results")
    expect(results).to_be_visible(timeout=15000)

    # Business view should be populated
    business = page.get_by_test_id("strategy-business")
    expect(business).to_be_visible()
    expect(business).to_contain_text("francecentral")


def test_strategy_summary_cards(page: Page, base_url: str) -> None:
    """Strategy results include summary cards with key metrics."""
    _navigate_to_strategy(page, base_url)

    page.get_by_test_id("strat-workload-name").fill("e2e-test-workload")
    _select_strat_sub(page)
    page.get_by_test_id("strat-instances").fill("2")

    page.get_by_test_id("strat-submit-btn").click()

    results = page.get_by_test_id("strategy-results")
    expect(results).to_be_visible(timeout=15000)

    # Summary cards area should show strategy type and instance count
    cards = page.locator("#strategy-summary-cards")
    expect(cards).to_contain_text("single region")
    expect(cards).to_contain_text("2")


def test_strategy_error_without_subscription(page: Page, base_url: str) -> None:
    """Submitting without a subscription shows an error."""
    _navigate_to_strategy(page, base_url)

    page.get_by_test_id("strat-workload-name").fill("test-workload")

    page.get_by_test_id("strat-submit-btn").click()

    error = page.get_by_test_id("strategy-error")
    expect(error).to_be_visible(timeout=5000)
    expect(error).to_contain_text("subscription")
