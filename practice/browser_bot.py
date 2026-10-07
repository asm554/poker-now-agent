"""Plays through real browser controls on our local practice table."""
import argparse
import asyncio
import json
from urllib.parse import urlparse
from .strategy import decide


async def snapshot(page):
    return json.loads(await page.locator('#table-state').text_content())


async def step(page):
    state = await snapshot(page)
    if not state or state.get('paused'):
        return False
    if state['done']:
        if min(state['stacks']) == 0:
            return False
        button = page.get_by_test_id('next-hand')
        await button.evaluate("(el, id) => {el.dataset.source = 'bot';el.dataset.expectedDecisionId=id}", state['decision_id'])
        await button.click(timeout=2000)
    elif state['is_my_turn']:
        decision = await asyncio.to_thread(decide, state)
        # The decision may have taken time; verify turn and version again.
        fresh = await snapshot(page)
        if fresh['paused'] or fresh['decision_id'] != state['decision_id']:
            return False
        report = {'decision_id': state['decision_id'], 'status': 'Entscheidung geprüft',
                  'action': decision.action.upper(), 'reason': decision.reason}
        await page.evaluate('data => window.reportBot(data)', report)
        if decision.action == 'raise':
            field = page.get_by_test_id('raise-amount')
            await field.fill(str(decision.amount))
            if await field.input_value() != str(decision.amount):
                raise RuntimeError('Einsatz konnte nicht bestätigt werden')
        button = page.get_by_test_id('action-' + decision.action)
        await button.evaluate("(el, id) => {el.dataset.source = 'bot';el.dataset.expectedDecisionId=id}", state['decision_id'])
        await button.click(timeout=2000)
    else:
        return False
    # A click alone is not a confirmed action. Observe the acknowledged version.
    await page.wait_for_function("id => {const s=JSON.parse(document.getElementById('table-state').textContent);return s.last_ack?.decision_id === id}", arg=state['decision_id'], timeout=5000)
    return True


async def run(url, headless=False, max_actions=None):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in ('', '/'):
        raise ValueError('Diese Version unterstützt nur unseren lokalen Spielgeldtisch')
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={'width': 1280, 'height': 900})
        await page.goto(url)
        await page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).version !== undefined")
        print('Browser bereit. Am Tisch „Übernahme starten“ klicken. Strg+C beendet den Bot.', flush=True)
        actions = 0
        try:
            while not page.is_closed():
                await page.evaluate('window.botHeartbeat()')
                if await step(page):
                    actions += 1
                    if max_actions is not None and actions >= max_actions:
                        break
                await asyncio.sleep(1.0)
        finally:
            if not page.is_closed():
                try:
                    await page.get_by_test_id('stop-bot').click(timeout=1000)
                except Exception:
                    pass
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description='Browserbot für unseren lokalen Spielgeldtisch')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == '__main__':
    main()
