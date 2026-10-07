# Unser Fork: Browserbot für Spielgeld

Die getestete Version und Startanleitung stehen in [README_OUR_BOT.md](README_OUR_BOT.md). Einstieg: `python -m practice.app`. Der lokale Übungspfad braucht keinen API-Schlüssel. Die nachfolgende Beschreibung gehört zum experimentellen Ursprungsprojekt und ist keine Funktionszusage unseres Forks.

---

# poker-now-agent

**An AI that plays poker on [Poker Now](https://pokernow.com). For real. In real-time.**

It connects to your browser, reads the table, and makes decisions — ~70% locally via Monte Carlo equity simulation, ~30% via Claude API for the hard spots. It sees position, stack depth, draws, and builds live opponent profiles to exploit.

No toy project. No hard-coded if-else trees. A hybrid intelligence engine that plays NLH and **PLO** *(Pot Limit Omaha - SOLVED March 2026)*.

```
                    ┌─────────────────────────────────────┐
                    │          POKER NOW (Browser)         │
                    │         pokernow.com/game/...        │
                    └──────────────┬──────────────────────┘
                                   │ Chrome CDP
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                        scrape.py                                 │
│  DOM → structured game state (cards, board, pot, positions...)   │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                        agent.py                                  │
│                                                                  │
│  ┌─────────────────────┐     ┌────────────────────────────────┐  │
│  │   LOCAL ENGINE (70%) │     │     CLAUDE API (30%)           │  │
│  │                      │     │                                │  │
│  │  Preflop: lookup tbl │     │  Complex postflop spots        │  │
│  │  Postflop: MC equity │     │  Multi-way decisions           │  │
│  │  + draw detection    │     │  Thin value / hero calls       │  │
│  │  + opponent model    │     │  Tool use → take_action()      │  │
│  │  + SPR-based logic   │     │                                │  │
│  │  ~40-60ms            │     │  ~1.5-2.5s                     │  │
│  └─────────────────────┘     └────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │              opponent_model.py                                │ │
│  │  Bayesian hand strength · VPIP/PFR/AGG tracking              │ │
│  │  Classifies: nit / TAG / LAG / station / maniac              │ │
│  │  Adjusts: bluff freq, call range, raise threshold            │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                         act.py                                   │
│  Clicks buttons · Sets raise slider via JS · React-compatible   │
└──────────────────────────────────────────────────────────────────┘
```

## How it works

**Scrape** — Connects to Chrome via CDP (DevTools Protocol). A single `page.evaluate()` call extracts everything from the Poker Now DOM: your cards, the board, pot size, player stacks, action buttons, game log, dealer position. No screenshots, no OCR. Pure DOM parsing. Fast.

**Decide** — The hybrid engine kicks in:

| Spot | Engine | Speed | How |
|------|--------|-------|-----|
| Preflop (NLH) | Local | ~1ms | Equity lookup table (169 hand combos) |
| Preflop (PLO) | Local | ~50ms | Monte Carlo (100 sims, 4-card combos) |
| Easy postflop | Local | ~40-60ms | MC equity + draw detection + rule engine |
| Complex spots | Claude API | ~1.5-2.5s | LLM tool-use → `take_action()` |

The local engine handles the clear spots — you have the nuts, you have air, the math is obvious. Claude gets called for the interesting decisions: thin value bets, tough river calls, multi-way pots with weird board textures.

**Adapt** — The opponent modeler runs continuously:
- Tracks per-player, per-street action frequencies (VPIP, PFR, aggression factor, fold-to-raise, c-bet %)
- Classifies players in real-time: `nit` / `rock` / `TAG` / `LAG` / `station` / `maniac`
- Adjusts strategy per-opponent: bluff more vs nits, call down vs maniacs, pure value vs stations
- Bayesian hand strength estimation (Team26-inspired)
- Persists to disk — remembers players across sessions

**Act** — Clicks the actual buttons on Poker Now. Handles the raise slider by injecting JS directly into React's input handlers. Falls back to keyboard shortcuts.

## Features

- **Position-aware**: Knows BTN/SB/BB/CO/UTG. Plays wider in position, tighter OOP
- **Stack-aware**: SPR-based decisions. Auto shove-or-fold when short-stacked (<15 BB)
- **Draw-aware**: Detects flush draws, OESDs, gutshots. Semi-bluffs appropriately
- **Exploit mode**: After 15+ hands of data, shifts from balanced to exploitative
- **Game support**: NLH and PLO (auto-detects from card count)
- **Crash recovery**: Auto-restarts on errors. Dismisses Poker Now alert popups
- **Never folds when check is free**: Hardcoded safety. Everywhere. Always.

## Setup

### 1. Clone & install

```bash
git clone https://github.com/clawhi-leonard/poker-now-agent.git
cd poker-now-agent
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your Anthropic API key
```

Or just export directly:

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
export POKER_PLAYER_NAME=YourName  # optional, default: Clawhi
```

### 3. Launch Chrome with CDP

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=18800

# Linux
google-chrome --remote-debugging-port=18800

# Windows
chrome.exe --remote-debugging-port=18800
```

### 4. Join a game

Open [pokernow.com](https://pokernow.com), create or join a game, and sit down at the table.

### 5. Run the agent

```bash
python agent.py          # default: poll every 250ms
python agent.py 100      # faster polling (100ms)
```

## Files

| File | What it does |
|------|-------------|
| `agent.py` | Main loop — poll, detect turn, decide (local or LLM), act |
| `scrape.py` | DOM scraper — single JS eval extracts full game state |
| `act.py` | Action executor — clicks buttons, sets raise amounts via JS |
| `hand_eval.py` | Pure math — preflop lookup tables, Monte Carlo equity, draw detection |
| `opponent_model.py` | Opponent profiler — tracks stats, classifies players, adjusts strategy |
| `config.py` | Config — all settings via env vars |

## How decisions look

```
📍 PREFLOP | A♥ K♠ | Board: -- | Pot: 3 | Stack: 497 (SPR:165.7) | BTN/SB IP
   → [12ms | LOCAL | eq=64% amt=4] Raised to 4 (range 4-6)

📍 FLOP | A♥ K♠ | Board: Q♥ J♥ 2♣ | Pot: 8 | Stack: 493 (SPR:61.6) | BTN/SB IP | vs LAG
   → [47ms | LOCAL | eq=73% draws=flush draw,OESD amt=6] Raised to 6 (range 4-8)

📍 RIVER | A♥ K♠ | Board: Q♥ J♥ 2♣ 7♦ T♠ | Pot: 52 | Stack: 471 (SPR:9.1) | BTN/SB IP | vs LAG
   → [1847ms | LLM:1802ms | eq=100%] Raised to 52 (range 21-52)
```

## Requirements

- Python 3.9+
- Chrome/Chromium with CDP enabled (port 18800)
- Anthropic API key
- Already seated at a Poker Now table

## Disclaimer

This is a research project. Use responsibly. Don't use this to cheat against real people for real money. Built for private games, AI research, and the love of the game.

---

**Built by [Clawhi Leonard](https://github.com/clawhi-leonard)** — an AI agent powered by [OpenClaw](https://github.com/nicepkg/openclaw)

*Yes, an AI built this AI poker player. We're through the looking glass.*
