import asyncio
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from playwright.async_api import async_playwright
from practice.server import make_server
from practice.browser_bot import snapshot, step


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = make_server(0, seed=11)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={'width': 1280, 'height': 900})
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        await self.page.goto(self.url)
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).version !== undefined")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=2)

    async def test_bot_plays_and_stop_blocks_actions(self):
        self.assertFalse(await step(self.page))
        await self.page.get_by_test_id('start-bot').click()
        await self.page.wait_for_function("!JSON.parse(document.getElementById('table-state').textContent).paused")
        for _ in range(16):
            await step(self.page)
        state = await snapshot(self.page)
        self.assertGreater(state['hand_id'], 1)
        self.assertTrue(any('Du:' in line or 'Du foldet' in line for line in state['log']))
        await self.page.get_by_test_id('stop-bot').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).paused")
        before = await snapshot(self.page)
        self.assertFalse(await step(self.page))
        self.assertEqual((await snapshot(self.page))['decision_id'], before['decision_id'])
        self.assertFalse(self.errors)

    async def test_stale_click_is_rejected(self):
        await self.page.get_by_test_id('next-hand').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).hand_id === 1")
        before = await snapshot(self.page)
        button = self.page.get_by_test_id('action-call')
        await button.evaluate("el => el.dataset.expectedDecisionId = 'stale'")
        await button.click()
        await self.page.wait_for_function("document.getElementById('error').textContent.includes('Veralteter')")
        self.assertEqual((await snapshot(self.page))['version'], before['version'])

    async def test_stop_during_calculation_discards_decision(self):
        from practice.strategy import decide
        await self.page.get_by_test_id('start-bot').click()
        await self.page.get_by_test_id('next-hand').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).hand_id === 1")
        started, release = threading.Event(), threading.Event()
        def slow_decide(state):
            started.set()
            release.wait(timeout=5)
            return decide(state)
        with patch('practice.browser_bot.decide', side_effect=slow_decide):
            task = asyncio.create_task(step(self.page))
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            await self.page.get_by_test_id('stop-bot').click()
            await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).paused")
            before = await snapshot(self.page)
            release.set()
            self.assertFalse(await task)
            self.assertEqual((await snapshot(self.page))['decision_id'], before['decision_id'])

    async def test_stopped_bot_click_cannot_submit(self):
        await self.page.get_by_test_id('next-hand').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).hand_id === 1")
        before = await snapshot(self.page)
        button = self.page.get_by_test_id('action-call')
        await button.evaluate("el => el.dataset.source = 'bot'")
        await button.click()
        await self.page.wait_for_function("document.getElementById('error').textContent.includes('gestoppt')")
        self.assertEqual((await snapshot(self.page))['version'], before['version'])

    async def test_raise_fill_readback_and_ack(self):
        await self.page.get_by_test_id('start-bot').click()
        await self.page.get_by_test_id('next-hand').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).hand_id === 1")
        state = await snapshot(self.page)
        amount = state['actions']['raise']['min']
        await self.page.get_by_test_id('raise-amount').fill(str(amount))
        self.assertEqual(await self.page.get_by_test_id('raise-amount').input_value(), str(amount))
        await self.page.get_by_test_id('action-raise').click()
        await self.page.wait_for_function("id => JSON.parse(document.getElementById('table-state').textContent).last_ack?.decision_id === id", arg=state['decision_id'])
        self.assertEqual((await snapshot(self.page))['last_ack']['action'], 'raise')

    async def test_mobile_and_desktop_render(self):
        await self.page.get_by_test_id('next-hand').click()
        await self.page.wait_for_function("JSON.parse(document.getElementById('table-state').textContent).hand_id === 1")
        output = Path('test-results')
        output.mkdir(exist_ok=True)
        await self.page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertFalse(self.errors)


if __name__ == '__main__':
    unittest.main()
