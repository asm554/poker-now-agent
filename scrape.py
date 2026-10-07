"""
Scrape Poker Now DOM into minimal text for LLM consumption.
Connects via CDP, grabs accessibility tree, parses to structured state.
Optimized for speed — no unnecessary processing.
"""
import asyncio
import json
import re
from playwright.async_api import async_playwright
from bot_config import CDP_ENDPOINT, PLAYER_NAME

SUIT_MAP = {"h": "♥", "s": "♠", "d": "♦", "c": "♣"}


async def get_poker_page():
    """Connect to existing browser and find poker tab."""
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP_ENDPOINT)
    for ctx in browser.contexts:
        for page in ctx.pages:
            if "pokernow.com" in page.url:
                return pw, page
    raise RuntimeError("No Poker Now tab found")


async def scrape_state(page) -> dict:
    """Extract game state from the page using JS for maximum speed."""
    # Single JS call — extracts everything from DOM in one shot for speed.
    # Poker Now uses React with specific class names. We also use fallback
    # selectors since the DOM structure can vary between game modes.
    state = await page.evaluate("""() => {
        const result = {
            is_my_turn: false,
            actions: [],
            my_cards: [],
            board: [],
            players: [],
            pot: null,
            pot_total: null,
            game_type: null,
            street: null,
            my_hand_strength: null,
            chat: []
        };

        // Game type — .table-game-type is the confirmed class
        const gtEl = document.querySelector('.table-game-type');
        if (gtEl) result.game_type = gtEl.textContent.trim();

        // Pot — .table-pot-size contains main pot + total
        const potArea = document.querySelector('.table-pot-size');
        if (potArea) {
            const nums = potArea.textContent.match(/\\d+/g);
            if (nums && nums.length >= 1) result.pot = parseInt(nums[0]);
            if (nums && nums.length >= 2) result.pot_total = parseInt(nums[nums.length - 1]);
        }

        // Board cards — inside .table-cards, each card is a child div
        // Board cards: .table-cards > .card-container > .card-flipper > .card > span.value + span.suit
        const boardArea = document.querySelector('.table-cards');
        if (boardArea) {
            boardArea.querySelectorAll('.card-container').forEach(container => {
                const valEl = container.querySelector('.value');
                const suitEls = container.querySelectorAll('.suit');
                if (valEl && suitEls.length > 0) {
                    const val = valEl.textContent.trim();
                    const suit = suitEls[suitEls.length - 1].textContent.trim();
                    if (val && /^[2-9TJQKA]|10$/.test(val)) result.board.push(val + suit);
                }
            });
        }

        // Find our seat — look for a .table-player that has our cards visible
        // Our player has card elements with visible values (opponents' cards are hidden)
        document.querySelectorAll('.table-player').forEach(player => {
            const nameEl = player.querySelector('a');
            if (!nameEl) return; // empty seat

            const name = nameEl.textContent.trim();
            const stackText = player.textContent;
            const stackMatch = stackText.match(/(?:^|\\s)(\\d+)(?:\\s|$)/g);

            // Find stack value — it's in a specific child, usually after the name
            let stack = '?';
            player.querySelectorAll('p, div').forEach(el => {
                const t = el.textContent.trim();
                if (/^\\d+$/.test(t) && parseInt(t) > 0 && parseInt(t) < 100000) {
                    stack = t;
                }
            });

            // Detect cards — same structure as board: .card-container > .card-flipper > .card > span.value + span.suit
            const cards = [];
            player.querySelectorAll('.card-container').forEach(container => {
                const valEl = container.querySelector('.value');
                const suitEls = container.querySelectorAll('.suit');
                if (valEl && suitEls.length > 0) {
                    const val = valEl.textContent.trim();
                    const suit = suitEls[suitEls.length - 1].textContent.trim();
                    if (val && /^[2-9TJQKA]|10$/.test(val)) cards.push(val + suit);
                }
            });

            const p = { name, stack, bet: '0', status: 'active' };

            // Current bet
            const betEl = player.querySelector('[class*=bet-value]');
            if (betEl) {
                const bm = betEl.textContent.match(/\\d+/);
                if (bm) p.bet = bm[0];
            }

            // Status text (fold, check, quitting, etc.)
            const statusTexts = ['Fold', 'check', 'Quitting', 'Away', 'Sitting Out'];
            player.querySelectorAll('p, div').forEach(el => {
                const t = el.textContent.trim();
                statusTexts.forEach(st => {
                    if (t.toLowerCase().includes(st.toLowerCase())) p.status = t;
                });
            });

            // Dealer button
            if (player.querySelector('[class*=dealer]')) p.dealer = true;

            // If this player has visible cards, it's us
            if (cards.length >= 2) {
                p.is_me = true;
                result.my_cards = cards;
            }

            result.players.push(p);
        });

        // Turn detection — check decision-current on our player element first (fastest/most reliable)
        const myPlayerEl = document.querySelector('.table-player.decision-current.you-player');
        if (myPlayerEl) {
            result.is_my_turn = true;
        }
        
        // Fallback: "Your Turn" text in action area
        const actionArea = document.querySelector('.action-buttons');
        if (actionArea) {
            if (actionArea.textContent.includes('Your Turn')) {
                result.is_my_turn = true;
            }

            // Only collect action buttons if it's actually our turn
            if (result.is_my_turn) {
                actionArea.querySelectorAll('button').forEach(btn => {
                    if (btn.disabled) return;
                    const text = btn.textContent.trim();
                    if (text && !text.includes('Extra Time') && text.length < 30) {
                        if (btn.classList.contains('suspended-action')) return;
                        if (text === 'Check or Fold' || text === 'Call Any') return;
                        result.actions.push(text);
                    }
                });
            }
        }

        // All-in detection — check if we're all-in
        document.querySelectorAll('.table-player').forEach(player => {
            const nameEl = player.querySelector('a');
            if (!nameEl) return;
            const pText = player.textContent.toLowerCase();
            if (pText.includes('all in')) {
                const name = nameEl.textContent.trim();
                // Find our player entry and mark it
                result.players.forEach(p => {
                    if (p.name === name) p.all_in = true;
                });
                if (player.classList.contains('you-player')) {
                    result.im_all_in = true;
                }
            }
        });

        // Hand strength — look for text near our cards
        document.querySelectorAll('.table-player').forEach(p => {
            if (p.querySelector('a')?.textContent?.trim() && result.my_cards.length > 0) {
                p.querySelectorAll('div, span').forEach(el => {
                    const t = el.textContent.trim().toLowerCase();
                    const handTypes = ['flush', 'straight', 'pair', 'two pair', 'trips',
                        'full house', 'quads', 'high card', 'set', 'boat'];
                    handTypes.forEach(ht => {
                        if (t.includes(ht) && t.length < 30) result.my_hand_strength = el.textContent.trim();
                    });
                });
            }
        });

        // Chat (last 5 messages)
        document.querySelectorAll('[class*=chat-message-container]').forEach(msg => {
            const author = msg.querySelector('a')?.textContent?.trim() || 'System';
            const body = msg.querySelector('[class*=chat-message-body], [class*=message-text]');
            const text = body?.textContent?.trim() || msg.textContent?.trim()?.substring(0, 100) || '';
            if (text && result.chat.length < 5) result.chat.push(author + ': ' + text);
        });

        // Game log — last 20 entries for action tracking
        result.log = [];
        document.querySelectorAll('.log-line, [class*=game-log] .log-message, .hand-log-line').forEach(line => {
            const text = line.textContent?.trim();
            if (text && result.log.length < 20) result.log.push(text);
        });

        // Position info — dealer button is a standalone element with class like "dealer-position-X"
        result.dealer_name = null;
        const dealerBtn = document.querySelector('[class*=dealer-button]');
        if (dealerBtn) {
            // Extract seat number from class like "dealer-position-1"
            const posMatch = dealerBtn.className.match(/dealer-position-(\d+)/);
            if (posMatch) {
                const seatNum = parseInt(posMatch[1]);
                // Find the player at that seat (table-player-X)
                const dealerPlayer = document.querySelector('.table-player-' + seatNum + ' a');
                if (dealerPlayer) {
                    result.dealer_name = dealerPlayer.textContent.trim();
                }
            }
        }

        return result;
    }""")

    # Determine street from board count
    board_count = len(state.get("board", []))
    if board_count == 0:
        state["street"] = "preflop"
    elif board_count == 3:
        state["street"] = "flop"
    elif board_count == 4:
        state["street"] = "turn"
    elif board_count == 5:
        state["street"] = "river"

    # Compute positions (heads-up: dealer=SB, other=BB)
    # Multi-way: dealer, SB=dealer+1, BB=dealer+2, then UTG, MP, CO, BTN
    players = [p for p in state.get("players", []) if p.get("name")]
    active_players = [p for p in players if "fold" not in p.get("status", "").lower() 
                      and "sitting" not in p.get("status", "").lower()
                      and "quitting" not in p.get("status", "").lower()]
    dealer_name = state.get("dealer_name")
    
    n = len(active_players)
    if n >= 2 and dealer_name:
        # Find dealer index
        names = [p["name"] for p in active_players]
        if dealer_name in names:
            d_idx = names.index(dealer_name)
            if n == 2:
                # Heads up: dealer = SB (acts first preflop, last postflop)
                active_players[d_idx]["position"] = "BTN/SB"
                active_players[(d_idx + 1) % n]["position"] = "BB"
            else:
                positions = []
                if n <= 3:
                    positions = ["BTN", "SB", "BB"]
                elif n <= 6:
                    positions = ["BTN", "SB", "BB"] + ["EP"] * (n - 5) + ["MP", "CO"] if n > 5 else ["BTN", "SB", "BB", "UTG", "CO", "MP"][:n]
                else:
                    positions = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"][:n]
                for i, pos in enumerate(positions):
                    idx = (d_idx + i) % n
                    active_players[idx]["position"] = pos
    
    # Set my_position
    for p in active_players:
        if p.get("is_me") and p.get("position"):
            state["my_position"] = p["position"]
    
    # Determine if we're in position (last to act postflop = dealer/BTN)
    state["in_position"] = state.get("my_position", "").startswith("BTN")

    return state


def state_to_text(state: dict) -> str:
    """Convert state dict to minimal text prompt for the LLM."""
    lines = []
    if state.get("game_type"):
        lines.append(f"Game: {state['game_type']}")

    lines.append(f"Street: {state.get('street', '?')}")

    if state.get("board"):
        lines.append(f"Board: {' '.join(state['board'])}")

    if state.get("my_cards"):
        lines.append(f"My cards: {' '.join(state['my_cards'])}")

    if state.get("my_hand_strength"):
        lines.append(f"Made hand: {state['my_hand_strength']}")

    pot_str = state.get("pot", "0")
    if state.get("pot_total"):
        pot_str = str(state["pot_total"])
    lines.append(f"Pot: {pot_str}")

    if state.get("my_position"):
        lines.append(f"My position: {state['my_position']} ({'IP' if state.get('in_position') else 'OOP'})")

    lines.append("Players:")
    for p in state.get("players", []):
        marker = " (ME)" if p.get("is_me") else ""
        dealer = " [D]" if p.get("dealer") else ""
        pos = f" [{p['position']}]" if p.get("position") else ""
        bet = f" bet:{p['bet']}" if p.get("bet") and p["bet"] != "0" else ""
        status = f" ({p['status']})" if p.get("status") and p["status"] not in ("active", "") else ""
        lines.append(f"  {p['name']}{marker}{pos}{dealer}: stack {p['stack']}{bet}{status}")

    if state.get("is_my_turn"):
        lines.append(f"\nMY TURN. Available: {', '.join(state.get('actions', []))}")
    else:
        lines.append("\nWaiting for opponent.")

    return "\n".join(lines)


async def get_state_text():
    """One-shot: connect, scrape, return text. For CLI use."""
    pw, page = await get_poker_page()
    state = await scrape_state(page)
    text = state_to_text(state)
    await pw.stop()
    return state, text


if __name__ == "__main__":
    state, text = asyncio.run(get_state_text())
    print(text)
    print("\n--- Raw JSON ---")
    print(json.dumps(state, indent=2))
