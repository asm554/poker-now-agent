"""
Poker Agent v3 - Hybrid: Local hand eval + LLM brain.

Strategy:
  1. Preflop: Lookup table equity → instant decisions (~1ms)
  2. Postflop easy spots: Monte Carlo + rule engine (~5-20ms)
  3. Postflop complex spots: Claude API (~1.5-2.5s)

This cuts API calls by ~70% and makes the agent much faster.
"""
import asyncio
import json
import time
import sys
import random
import anthropic
from bot_config import CDP_ENDPOINT, PLAYER_NAME, MODEL, API_KEY
from scrape import get_poker_page, scrape_state, state_to_text
from act import execute_action
from hand_eval import get_equity, detect_draws, preflop_equity
from opponent_model import OpponentModel
from tracker import SessionTracker

opp_tracker = tracker = OpponentModel()
session_tracker = None  # initialized in run()

# ---- Local Decision Engine ----

def local_decision(state, equity, draws):
    """
    Adaptive decision engine. Uses equity + opponent model to decide.
    Returns (action, amount) or None if needs LLM.
    """
    import re as _re
    actions_str = " ".join(state.get("actions", []))
    can_check = "Check" in actions_str
    can_raise = "Raise" in actions_str or "Bet" in actions_str
    can_call = "Call" in actions_str
    street = state.get("street", "preflop")
    pot = int(state.get("pot_total") or state.get("pot") or 0)

    call_amount = 0
    for a in state.get("actions", []):
        m = _re.search(r"Call\s+(\d+)", a)
        if m:
            call_amount = int(m.group(1))

    my_stack = 0
    for p in state.get("players", []):
        if p.get("is_me"):
            try: my_stack = int(p["stack"])
            except: my_stack = 1000

    # Stack-to-pot ratio - key metric for decision depth
    spr = my_stack / max(pot, 1)

    # Helper: cap raise to stack, and if raise would commit >60% of stack, just shove
    def size_raise(target_amount):
        amt = min(target_amount, my_stack)
        amt = max(amt, 2)
        if amt >= my_stack * 0.6:
            return my_stack  # commit / shove
        return amt

    # Position awareness
    in_position = state.get("in_position", False)
    my_pos = state.get("my_position", "?")
    
    # NEVER fold when check is free
    if can_check and not can_call:
        if equity > 60 and can_raise:
            amount = size_raise(int(pot * random.uniform(0.5, 0.9)))
            return ("raise", amount)
        # In position with decent equity — bet more often for value/info
        if in_position and equity > 45 and can_raise and random.random() < 0.35:
            amount = size_raise(max(2, int(pot * random.uniform(0.4, 0.7))))
            return ("raise", amount)
        return ("check", None)

    # Get opponent adjustments
    opponents = [p["name"] for p in state.get("players", [])
                 if not p.get("is_me") and p.get("status", "active") == "active"]

    # Use the most relevant opponent (the one who last acted aggressively, or first active)
    adj = {"bluff_freq": 0.15, "call_wider": 0, "raise_more": 0, "fold_more": 0}
    opp_type = "unknown"
    for opp in opponents:
        opp_adj = tracker.get_adjustments(opp)
        opp_type = tracker.classify(opp)
        if opp_type != "unknown":
            adj = opp_adj
            break

    # Adjust equity thresholds based on opponent
    call_adj = adj.get("call_wider", 0)  # positive = call with worse hands
    raise_adj = adj.get("raise_more", 0)  # positive = raise with worse hands
    bluff_freq = adj.get("bluff_freq", 0.15)

    pot_odds = call_amount / max(pot + call_amount, 1) * 100 if call_amount > 0 else 0
    has_flush_draw = "flush draw" in draws
    has_oesd = "OESD" in draws
    has_draw = len(draws) > 0

    # ---- Bayesian-adjusted equity (Team26 approach) ----
    opp_hs = tracker.get_opp_hand_strength()
    if opp_hs > 0.05:
        my_wr = equity / 100.0
        bayes = my_wr * (1 - opp_hs) / (my_wr * (1 - opp_hs) + opp_hs * (1 - my_wr) + 0.001)
        blended = equity * 0.5 + bayes * 100 * 0.5
    else:
        blended = equity

    # Apply opponent-specific adjustments
    effective_equity = blended + call_adj

    # ---- PREFLOP ----
    if street == "preflop":
        # Position adjustments: play wider in position, tighter OOP
        pos_adj = 0
        if in_position or my_pos in ("BTN", "BTN/SB", "CO"):
            pos_adj = 5  # wider range in position
        elif my_pos in ("BB",):
            pos_adj = 3  # defend BB a bit wider
        elif my_pos in ("UTG", "UTG+1", "EP"):
            pos_adj = -5  # tighter from early position
        
        raise_thresh = 65 - raise_adj - pos_adj
        call_thresh = 40 - call_adj - pos_adj

        # Short stack (<15 BB): tighten up, shove or fold with strong hands
        if spr < 5:
            if equity >= raise_thresh and can_raise:
                return ("raise", my_stack)  # shove
            elif equity >= call_thresh:
                return ("call", None) if can_call else ("check", None)
            elif can_check:
                return ("check", None)
            else:
                return ("fold", None)

        if equity >= raise_thresh:
            amount = size_raise(max(4, int(pot * random.uniform(0.7, 1.0))))
            return ("raise", amount) if can_raise else ("call", None)
        elif equity >= 55 - raise_adj:
            if can_raise and random.random() < 0.65:
                amount = size_raise(max(4, int(pot * random.uniform(0.5, 0.8))))
                return ("raise", amount)
            return ("call", None)
        elif equity >= call_thresh:
            # Don't call if it's a huge % of stack with marginal hand
            if call_amount > my_stack * 0.25 and equity < 50:
                return ("fold", None)
            if can_raise and random.random() < (bluff_freq + 0.1):
                amount = size_raise(max(4, int(pot * random.uniform(0.5, 0.7))))
                return ("raise", amount)
            return ("call", None) if can_call else ("check", None)
        elif equity >= 33:
            if call_amount <= max(2, pot * 0.3) and call_amount <= my_stack * 0.1:
                return ("call", None)
            return ("fold", None)
        else:
            if call_amount <= 2 and call_amount <= my_stack * 0.05:
                return ("call", None)
            return ("fold", None)

    # ---- POSTFLOP ----

    # ---- POSTFLOP: Low SPR = commit or fold, no fancy play ----
    if spr < 3:
        if effective_equity >= 50:
            if can_raise:
                return ("raise", my_stack)  # shove - we're pot committed
            return ("call", None) if can_call else ("check", None)
        elif effective_equity >= 35 and has_draw:
            return ("call", None) if can_call else ("check", None)
        elif can_check:
            return ("check", None)
        elif call_amount <= my_stack * 0.15:
            return ("call", None) if can_call else ("fold", None)
        else:
            return ("fold", None)

    # Position adjustments for postflop
    # In position: bet/raise more (info advantage), call wider
    # Out of position: check more, trap less, tighter calls
    pos_call_adj = 5 if in_position else -3
    pos_raise_adj = 3 if in_position else -2
    
    # Strong hand (exploitative: tighter value range vs stations, wider vs others)
    value_thresh = 60 - call_adj - pos_raise_adj
    if effective_equity >= value_thresh:
        if can_raise:
            if opp_type == "station":
                amount = size_raise(max(2, int(pot * random.uniform(0.7, 1.0))))
            elif opp_type in ("nit", "rock"):
                amount = size_raise(max(2, int(pot * random.uniform(0.4, 0.6))))
            else:
                amount = size_raise(max(2, int(pot * random.uniform(0.5, 0.85))))
            return ("raise", amount)
        return ("call", None) if can_call else ("check", None)

    # Drawing hand (semi-bluff)
    if effective_equity >= 35 and has_draw:
        if has_flush_draw or has_oesd:
            # Don't semi-bluff if it costs too much of our stack
            if can_raise and random.random() < 0.55 and pot < my_stack * 0.4:
                amount = size_raise(max(2, int(pot * random.uniform(0.5, 0.8))))
                return ("raise", amount)
        if can_call and pot_odds < effective_equity + 10:
            # Don't call draws if it's too expensive relative to stack
            if call_amount <= my_stack * 0.3:
                return ("call", None)
            else:
                return ("fold", None)
        if can_check:
            return ("check", None)
        return ("call", None) if can_call else ("fold", None)

    # Medium hand
    if effective_equity >= 35:
        if can_call and pot_odds < effective_equity:
            if call_amount <= my_stack * 0.25:
                return ("call", None)
            elif effective_equity >= 45:
                return ("call", None)  # stronger medium hands can call bigger
            else:
                return ("fold", None)
        if can_check:
            return ("check", None)
        if can_call and opp_type in ("LAG", "maniac") and pot_odds < effective_equity + 15:
            return ("call", None)
        return ("fold", None) if not can_check else ("check", None)

    # Weak hand - bluff or give up
    if effective_equity >= 20:
        if can_check:
            return ("check", None)
        # Only bluff if it won't cost too much of stack
        if can_raise and random.random() < bluff_freq and street != "river" and pot < my_stack * 0.3:
            amount = size_raise(max(2, int(pot * random.uniform(0.5, 0.75))))
            return ("raise", amount)
        if can_call and pot_odds < effective_equity and call_amount <= my_stack * 0.15:
            return ("call", None)
        return ("fold", None)

    # Junk
    if can_check:
        return ("check", None)
    if can_raise and random.random() < bluff_freq * 0.5 and opp_type in ("nit", "rock") and pot < my_stack * 0.2:
        amount = size_raise(max(2, int(pot * random.uniform(0.6, 0.9))))
        return ("raise", amount)
    return ("fold", None)


# ---- LLM Decision (complex spots only) ----

SYSTEM_PROMPT = """Expert poker player. Call take_action IMMEDIATELY.

RULES:
- NEVER fold when you can check for free.
- Pot limit game. Max raise = pot size.
- Use the equity and draw info provided - they're computed accurately.
- Consider opponent tendencies if provided.
- Be aggressive with strong hands and draws. Bet for value AND protection."""

TOOLS = [{
    "name": "take_action",
    "description": "Poker action. NEVER fold when check is available.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["check", "fold", "call", "raise"]},
            "amount": {"type": "integer", "description": "Raise amount"}
        },
        "required": ["action"]
    }
}]

client = None


async def llm_decision(state, state_text, equity, draws):
    """Ask Claude for complex decisions."""
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


# ---- Main Loop ----

async def decide_and_act(page, state, state_text):
    t0 = time.time()

    actions_str = " ".join(state.get("actions", []))
    can_check = "Check" in actions_str

    # Get equity
    my_cards = state.get("my_cards", [])
    board = state.get("board", [])
    num_opponents = max(1, sum(1 for p in state.get("players", [])
                                if not p.get("is_me") and p.get("status","active") == "active"))

    equity = get_equity(my_cards, board if board else None, num_opponents)
    draws = detect_draws(my_cards, board) if board else []

    # Try local decision first
    decision = local_decision(state, equity, draws)

    use_llm = False
    if decision is None:
        use_llm = True

    # Safety override
    if decision and decision[0] == "fold" and can_check:
        decision = ("check", None)

    # Enforce sane raise size
    if decision and decision[0] == "raise":
        amt = decision[1]
        current_pot = int(state.get("pot_total") or state.get("pot") or 0)
        # Minimum: at least the big blind (2) or 40% of pot
        min_raise = max(4, int(current_pot * 0.4))
        # Maximum: pot size (pot limit)
        max_raise = max(min_raise, current_pot)
        if not amt or amt < min_raise:
            amt = min_raise
        if amt > max_raise:
            amt = max_raise
        decision = (decision[0], amt)

    street = state.get("street", "preflop")
    
    if use_llm:
        try:
            action, amount = await llm_decision(state, state_text, equity, draws)
            if action == "fold" and can_check:
                action = "check"
            api_ms = int((time.time() - t0) * 1000)
            result = await execute_action(page, action, amount)
            total_ms = int((time.time() - t0) * 1000)
            # Track action + LLM usage
            if session_tracker:
                session_tracker.record_action(street, action)
                session_tracker.record_llm_call()
            return f"[{total_ms}ms | LLM:{api_ms}ms | eq={equity:.0f}%] {result}"
        except Exception as e:
            fallback = "check" if can_check else "call"
            result = await execute_action(page, fallback)
            ms = int((time.time() - t0) * 1000)
            if session_tracker:
                session_tracker.record_action(street, fallback)
                session_tracker.record_llm_call()
            return f"[{ms}ms | LLM ERR] {result}: {str(e)[:40]}"
    else:
        action, amount = decision
        result = await execute_action(page, action, amount)
        ms = int((time.time() - t0) * 1000)
        # Track action
        if session_tracker:
            session_tracker.record_action(street, action)
            session_tracker.record_local_decision()
        draw_str = f" draws={','.join(draws)}" if draws else ""
        amt_str = f" amt={amount}" if amount else ""
        return f"[{ms}ms | LOCAL | eq={equity:.0f}%{draw_str}{amt_str}] {result}"


async def run(poll_ms=250):
    global session_tracker
    print(f"🃏 Poker Agent v3 | {PLAYER_NAME} | {MODEL}")
    print(f"   Hybrid: preflop lookup + MC equity + LLM for complex spots")
    print(f"   Connecting to {CDP_ENDPOINT}...")

    pw, page = await get_poker_page()
    print(f"   ✅ Connected | Polling every {poll_ms}ms\n")

    # Initialize session tracker
    session_tracker = SessionTracker(bot_name=PLAYER_NAME, buy_in=1000, bot_version="v3", mode="single")
    print(f"   📊 Session tracker started: {session_tracker.session['id']}")

    last_act_time = 0
    last_state_key = None
    last_cards = None

    try:
        while True:
            # Dismiss alerts
            try:
                alert = await page.query_selector('.alert-1-container button:has-text("Confirm"), .alert-1-container button:has-text("Ok")')
                if alert:
                    cb = await page.query_selector('.alert-1-container input[type="checkbox"]')
                    if cb: await cb.click()
                    await alert.click()
                    await asyncio.sleep(0.3)
            except:
                pass

            state = await scrape_state(page)

            # New hand detection
            cards = tuple(state.get("my_cards", []))
            if cards and cards != last_cards and last_cards:
                opp_tracker.end_hand()
                opp_tracker.new_hand()
                # Track stack at start of each new hand
                my_stack_now = None
                for p in state.get("players", []):
                    if p.get("is_me"):
                        try: my_stack_now = int(p["stack"])
                        except: pass
                session_tracker.new_hand(my_stack_now)
            last_cards = cards

            if state.get("is_my_turn"):
                now = time.time()

                # Skip if we're all-in (no action to take)
                if state.get("im_all_in"):
                    await asyncio.sleep(2.0)
                    continue

                # Skip if no real action buttons
                actions_text = " ".join(state.get("actions", []))
                if not any(k in actions_text for k in ["Check", "Call", "Fold", "Raise", "Bet"]):
                    await asyncio.sleep(1.0)
                    continue

                # Dedup: act once per decision point
                # Key = cards + board + call present + pot (catches re-raises on same street)
                has_call = any("Call" in a for a in state.get("actions", []))
                pot_val = int(state.get("pot_total") or state.get("pot") or 0)
                state_key = (cards, tuple(state.get("board", [])), has_call, pot_val)

                if state_key == last_state_key and now - last_act_time < 3.0:
                    await asyncio.sleep(0.2)
                    continue

                # Only skip if we have 0 stack AND no real actions (true runout)
                my_stack_val = 0
                for p in state.get("players", []):
                    if p.get("is_me"):
                        try: my_stack_val = int(p["stack"])
                        except: pass

                real_actions = [a for a in state.get("actions", [])
                               if any(k in a for k in ["Call", "Fold", "Raise", "Bet"])]
                if my_stack_val == 0 and not real_actions:
                    await asyncio.sleep(1.0)
                    continue

                street = state.get("street", "?")
                board_str = " ".join(state.get("board", ["--"]))
                cards_str = " ".join(state.get("my_cards", []))
                pot = state.get("pot_total", state.get("pot", "?"))
                text = state_to_text(state)

                # Feed opponent actions from game log into tracker
                for p in state.get("players", []):
                    if not p.get("is_me") and p.get("status") and p["status"] != "active":
                        status = p["status"].lower()
                        if "fold" in status:
                            opp_tracker.record_action(p["name"], "fold", street)
                
                # Parse game log for detailed opponent actions
                import re as _re
                for log_line in state.get("log", []):
                    # Poker Now log format: "Player raises to X" / "Player calls X" / "Player checks" / "Player folds"
                    for p in state.get("players", []):
                        if p.get("is_me"):
                            continue
                        pname = p.get("name", "")
                        if not pname or pname not in log_line:
                            continue
                        ll = log_line.lower()
                        if "raises" in ll or "bets" in ll:
                            opp_tracker.record_action(pname, "raise", street)
                        elif "calls" in ll:
                            opp_tracker.record_action(pname, "call", street)
                        elif "checks" in ll:
                            opp_tracker.record_action(pname, "check", street)
                        elif "folds" in ll:
                            opp_tracker.record_action(pname, "fold", street)

                # Get exploit info for logging
                opp_names = [p["name"] for p in state.get("players", [])
                            if not p.get("is_me") and p.get("status","active") == "active"]
                exploit_info = ""
                for opp in opp_names[:1]:
                    ot = tracker.classify(opp)
                    if ot != "unknown":
                        exploit_info = f" | vs {ot}"

                my_stk = "?"
                for p in state.get("players", []):
                    if p.get("is_me"):
                        my_stk = p.get("stack", "?")
                cur_spr = f"{int(my_stk) / max(int(pot or 1), 1):.1f}" if str(my_stk).isdigit() and pot else "?"
                pos_str = state.get("my_position", "?")
                ip_str = "IP" if state.get("in_position") else "OOP"
                print(f"📍 {street.upper()} | {cards_str} | Board: {board_str} | Pot: {pot} | Stack: {my_stk} (SPR:{cur_spr}) | {pos_str} {ip_str}{exploit_info}")

                result = await decide_and_act(page, state, text)
                print(f"   → {result}")
                last_act_time = time.time()
                last_state_key = state_key

                # Wait for DOM to settle after action
                await asyncio.sleep(0.8)
            else:
                await asyncio.sleep(poll_ms / 1000)

    except KeyboardInterrupt:
        print("\n👋 Stopping.")
    finally:
        # Save session data
        if session_tracker:
            # Get final stack
            try:
                final_state = await scrape_state(page)
                for p in final_state.get("players", []):
                    if p.get("is_me"):
                        try:
                            final_stack = int(p["stack"])
                            session_tracker.record_stack(final_stack)
                        except:
                            pass
            except:
                pass
            
            result = session_tracker.end_session()
            print(f"\n📊 SESSION REPORT")
            print(f"   Hands: {result['hands_played']} | PnL: {result['pnl']:+d}")
            print(f"   VPIP: {result.get('vpip', 0)}% | PFR: {result.get('pfr', 0)}%")
            print(f"   BB/100: {result.get('bb_per_100', 0)}")
            print(f"   LLM calls: {result['llm_calls']} | Local: {result['local_decisions']}")
            print(f"   Peak: {result['peak_stack']} | Min: {result['min_stack']}")
            print(f"   Saved to data/sessions.json")
        
        await pw.stop()


if __name__ == "__main__":
    poll = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    while True:
        try:
            asyncio.run(run(poll_ms=poll))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n⚠️ Crashed: {e}")
            print("   Restarting in 3s...")
            import time as _t; _t.sleep(3)
