"""
Poker Agent v4 - Robust & Autonomous

Improvements over v3:
- Auto-reconnect on browser disconnect
- Exponential backoff on errors  
- Timeout on all page operations
- Auto-dismiss all popups/modals
- Auto-rebuy when busted
- Auto-start game when players seated
- Never crashes — logs and continues
"""
import asyncio
import json
import time
import sys
import random
import traceback
import anthropic
from bot_config import CDP_ENDPOINT, PLAYER_NAME, MODEL, API_KEY
from scrape import get_poker_page, scrape_state, state_to_text
from act import execute_action
from hand_eval import get_equity, detect_draws, preflop_equity
from opponent_model import OpponentModel

# Import the decision engine from agent.py
from agent import local_decision, SYSTEM_PROMPT, TOOLS, opp_tracker as tracker

client = None


# ──────────────────────────────────────────────
# Robust Wrappers
# ──────────────────────────────────────────────

async def safe_scrape(page, timeout=5.0):
    """Scrape state with timeout and error handling."""
    try:
        return await asyncio.wait_for(scrape_state(page), timeout=timeout)
    except asyncio.TimeoutError:
        print("   ⏱️  Scrape timeout")
        return None
    except Exception as e:
        print(f"   ⚠️ Scrape error: {str(e)[:60]}")
        return None


async def safe_action(page, action, amount=None, timeout=10.0):
    """Execute action with timeout."""
    try:
        return await asyncio.wait_for(execute_action(page, action, amount), timeout=timeout)
    except asyncio.TimeoutError:
        return "Action timeout"
    except Exception as e:
        return f"Action error: {str(e)[:50]}"


async def dismiss_all_alerts(page):
    """Aggressively dismiss any popup/modal/alert."""
    selectors = [
        '.alert-1-container button:has-text("Confirm")',
        '.alert-1-container button:has-text("Ok")',
        '.alert-1-container button:has-text("OK")',
        '.alert-1-container button:has-text("Yes")',
        'button:has-text("Close")',
        '.modal button:has-text("OK")',
        '.modal button:has-text("Close")',
        'button:has-text("Got it")',
        'button:has-text("Dismiss")',
        'button:has-text("Continue")',
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                try:
                    cb = await page.query_selector('.alert-1-container input[type="checkbox"]')
                    if cb: await cb.click()
                except: pass
                await el.click()
                await asyncio.sleep(0.3)
        except:
            continue


async def auto_rebuy(page, amount=1000):
    """Auto-rebuy when stack is 0."""
    for sel in ['button:has-text("Re-buy")', 'button:has-text("Rebuy")',
                'button:has-text("Add chips")', 'button:has-text("Buy In")']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(1)
                inp = await page.query_selector('input[type="number"], input[type="text"]')
                if inp and await inp.is_visible():
                    await inp.fill(str(amount))
                    await asyncio.sleep(0.3)
                for csel in ['button:has-text("Confirm")', 'button:has-text("OK")',
                              'button:has-text("Buy")', 'input[type="submit"]']:
                    btn = await page.query_selector(csel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        print(f"   💰 Auto-rebuy: {amount}")
                        await asyncio.sleep(1)
                        return True
        except:
            pass
    return False


async def try_start_game(page):
    """Try to start the game if we're the host."""
    for sel in ['button:has-text("Start")', 'button:has-text("Start Game")']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                print("   ▶️  Started game!")
                await asyncio.sleep(1)
                return True
        except:
            continue
    return False


# ──────────────────────────────────────────────
# LLM Decision (with retries)
# ──────────────────────────────────────────────

async def llm_decision(state, state_text, equity, draws, retries=2):
    """Ask Claude for complex decisions, with retry."""
    global client
    if not API_KEY or not MODEL:
        raise RuntimeError('LLM requires ANTHROPIC_API_KEY and ANTHROPIC_MODEL')
    if client is None:
        client = anthropic.AsyncAnthropic(api_key=API_KEY)
    prompt = state_text
    prompt += f"\n\nEquity: {equity:.0f}%"
    if draws:
        prompt += f" | Draws: {', '.join(draws)}"
    reads = tracker.summary()
    if reads and "No opponent" not in reads:
        prompt += f"\nOpponent reads:\n{reads}"

    for attempt in range(retries + 1):
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=80,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                tool_choice={"type": "tool", "name": "take_action"},
                messages=[{"role": "user", "content": prompt}]
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "take_action":
                    return block.input.get("action", "check"), block.input.get("amount")
            return "check", None
        except Exception as e:
            if attempt < retries:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                print(f"   ⚠️ LLM failed after {retries+1} attempts: {str(e)[:50]}")
                return "check", None


# ──────────────────────────────────────────────
# Decision + Action
# ──────────────────────────────────────────────

async def decide_and_act(page, state, state_text):
    t0 = time.time()
    actions_str = " ".join(state.get("actions", []))
    can_check = "Check" in actions_str

    my_cards = state.get("my_cards", [])
    board = state.get("board", [])
    num_opponents = max(1, sum(1 for p in state.get("players", [])
                                if not p.get("is_me") and p.get("status","active") == "active"))

    equity = get_equity(my_cards, board if board else None, num_opponents)
    draws = detect_draws(my_cards, board) if board else []

    decision = local_decision(state, equity, draws)
    use_llm = decision is None

    if decision and decision[0] == "fold" and can_check:
        decision = ("check", None)

    if decision and decision[0] == "raise":
        amt = decision[1]
        pot = int(state.get("pot_total") or state.get("pot") or 0)
        min_raise = max(4, int(pot * 0.4))
        max_raise = max(min_raise, pot)
        if not amt or amt < min_raise: amt = min_raise
        if amt > max_raise: amt = max_raise
        decision = (decision[0], amt)

    if use_llm:
        try:
            action, amount = await llm_decision(state, state_text, equity, draws)
            if action == "fold" and can_check:
                action = "check"
            result = await safe_action(page, action, amount)
            ms = int((time.time() - t0) * 1000)
            return f"[{ms}ms|LLM|eq={equity:.0f}%] {result}"
        except Exception as e:
            result = await safe_action(page, "check" if can_check else "call")
            ms = int((time.time() - t0) * 1000)
            return f"[{ms}ms|ERR] {result}: {str(e)[:40]}"
    else:
        action, amount = decision
        result = await safe_action(page, action, amount)
        ms = int((time.time() - t0) * 1000)
        draws_str = f" d={','.join(draws)}" if draws else ""
        return f"[{ms}ms|LOCAL|eq={equity:.0f}%{draws_str}] {result}"


# ──────────────────────────────────────────────
# Connection Management
# ──────────────────────────────────────────────

async def connect_with_retry(max_retries=50, base_delay=3):
    """Connect to browser with exponential backoff."""
    for attempt in range(max_retries):
        try:
            pw, page = await get_poker_page()
            # Verify page is responsive
            await asyncio.wait_for(page.title(), timeout=5)
            return pw, page
        except Exception as e:
            delay = min(base_delay * (1.5 ** attempt), 60)
            print(f"   🔄 Connect attempt {attempt+1} failed: {str(e)[:50]}")
            print(f"      Retrying in {delay:.0f}s...")
            await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to connect after {max_retries} attempts")


async def check_page_alive(page):
    """Check if the page is still responsive."""
    try:
        await asyncio.wait_for(page.title(), timeout=3)
        return True
    except:
        return False


# ──────────────────────────────────────────────
# Main Loop
# ──────────────────────────────────────────────

async def run(poll_ms=250):
    print(f"🃏 Poker Agent v4 (Robust) | {PLAYER_NAME} | {MODEL}")
    print(f"   Hybrid: preflop lookup + MC equity + LLM for complex spots")
    print(f"   Auto-reconnect | Auto-rebuy | Auto-dismiss alerts")
    print(f"   Connecting to {CDP_ENDPOINT}...")

    pw, page = await connect_with_retry()
    print(f"   ✅ Connected | Polling every {poll_ms}ms\n")

    last_act_time = 0
    last_state_key = None
    last_cards = None
    consecutive_errors = 0
    hands_played = 0
    health_check_interval = 30  # seconds
    last_health_check = time.time()

    try:
        while True:
            try:
                # Periodic health check
                now = time.time()
                if now - last_health_check > health_check_interval:
                    if not await check_page_alive(page):
                        print("   🔄 Page unresponsive, reconnecting...")
                        try: await pw.stop()
                        except: pass
                        pw, page = await connect_with_retry()
                        print("   ✅ Reconnected!")
                        consecutive_errors = 0
                    last_health_check = now

                # Dismiss alerts
                await dismiss_all_alerts(page)

                # Try to start game
                await try_start_game(page)

                # Scrape
                state = await safe_scrape(page)
                if state is None:
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        print("   🔄 Too many scrape failures, reconnecting...")
                        try: await pw.stop()
                        except: pass
                        pw, page = await connect_with_retry()
                        consecutive_errors = 0
                    await asyncio.sleep(1)
                    continue

                consecutive_errors = 0

                # New hand detection
                cards = tuple(state.get("my_cards", []))
                if cards and cards != last_cards and last_cards:
                    tracker.end_hand()
                    tracker.new_hand()
                    hands_played += 1
                    if hands_played % 25 == 0:
                        print(f"\n   📊 Stats: {hands_played} hands | Opponents tracked: {len(tracker.players)}")
                        print(f"   {tracker.summary()}\n")
                last_cards = cards

                if state.get("is_my_turn"):
                    if state.get("im_all_in"):
                        await asyncio.sleep(2.0)
                        continue

                    actions_text = " ".join(state.get("actions", []))
                    if not any(k in actions_text for k in ["Check", "Call", "Fold", "Raise", "Bet"]):
                        await asyncio.sleep(1.0)
                        continue

                    has_call = any("Call" in a for a in state.get("actions", []))
                    pot_val = int(state.get("pot_total") or state.get("pot") or 0)
                    state_key = (cards, tuple(state.get("board", [])), has_call, pot_val)

                    if state_key == last_state_key and time.time() - last_act_time < 3.0:
                        await asyncio.sleep(0.2)
                        continue

                    # Log the spot
                    street = state.get("street", "?")
                    board_str = " ".join(state.get("board", ["--"]))
                    cards_str = " ".join(state.get("my_cards", []))
                    pot = state.get("pot_total", state.get("pot", "?"))
                    my_stk = "?"
                    for p in state.get("players", []):
                        if p.get("is_me"): my_stk = p.get("stack", "?")
                    pos_str = state.get("my_position", "?")
                    ip_str = "IP" if state.get("in_position") else "OOP"

                    # Feed opponent actions into tracker
                    import re as _re
                    for log_line in state.get("log", []):
                        for p in state.get("players", []):
                            if p.get("is_me"): continue
                            pname = p.get("name", "")
                            if not pname or pname not in log_line: continue
                            ll = log_line.lower()
                            if "raises" in ll or "bets" in ll:
                                tracker.record_action(pname, "raise", street)
                            elif "calls" in ll:
                                tracker.record_action(pname, "call", street)
                            elif "checks" in ll:
                                tracker.record_action(pname, "check", street)
                            elif "folds" in ll:
                                tracker.record_action(pname, "fold", street)

                    print(f"📍 {street.upper()} | {cards_str} | Board: {board_str} | Pot: {pot} | Stack: {my_stk} | {pos_str} {ip_str}")

                    text = state_to_text(state)
                    result = await decide_and_act(page, state, text)
                    print(f"   → {result}")

                    last_act_time = time.time()
                    last_state_key = state_key
                    await asyncio.sleep(0.8)
                else:
                    # Not our turn — check for rebuy
                    my_stack = 0
                    for p in state.get("players", []):
                        if p.get("is_me"):
                            try: my_stack = int(p["stack"])
                            except: pass
                    if my_stack == 0:
                        await auto_rebuy(page)

                    await asyncio.sleep(poll_ms / 1000)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                consecutive_errors += 1
                print(f"   ⚠️ Loop error ({consecutive_errors}): {str(e)[:80]}")
                if consecutive_errors > 15:
                    print("   🔄 Too many errors, reconnecting...")
                    try: await pw.stop()
                    except: pass
                    try:
                        pw, page = await connect_with_retry()
                        consecutive_errors = 0
                        print("   ✅ Reconnected!")
                    except Exception as ce:
                        print(f"   ❌ Reconnect failed: {ce}")
                        await asyncio.sleep(30)
                else:
                    await asyncio.sleep(min(2 ** min(consecutive_errors, 5), 30))

    except KeyboardInterrupt:
        print(f"\n👋 Stopping. Hands played: {hands_played}")
    finally:
        tracker.save()
        try: await pw.stop()
        except: pass


if __name__ == "__main__":
    poll = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    while True:
        try:
            asyncio.run(run(poll_ms=poll))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n⚠️ Top-level crash: {e}")
            traceback.print_exc()
            print("   Restarting in 5s...")
            time.sleep(5)
