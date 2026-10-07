"""Transparent baseline strategy; equity is against unknown random hands."""
from dataclasses import dataclass
from hand_eval import get_equity, validate_cards


@dataclass(frozen=True)
class Decision:
    action: str
    amount: int | None
    reason: str
    equity: float


def decide(state):
    validate_cards(state['my_cards'], state['board'])
    if not state['is_my_turn'] or state['done']:
        raise ValueError('Kein eigener Zug')
    legal = state['actions']
    equity = get_equity(state['my_cards'], state['board']) / 100
    call = legal.get('call', 0)
    odds = call / max(state['pot'] + call, 1)
    if 'raise' in legal and equity >= (0.70 if call else 0.62):
        limits = legal['raise']
        target = max(state['bets']) + max(10, int(state['pot'] * 0.6))
        amount = max(limits['min'], min(target, limits['max']))
        return Decision('raise', amount, f'Value: {equity:.0%} Equity gegen unbekannte Karten; Raise auf {amount}.', equity)
    if 'check' in legal:
        return Decision('check', None, f'Kostenlos ansehen; geschätzte Equity {equity:.0%}.', equity)
    if 'call' in legal and equity >= odds + 0.08:
        return Decision('call', None, f'Equity {equity:.0%}, benötigter Potanteil {odds:.0%}; Call {call}.', equity)
    if 'fold' in legal:
        return Decision('fold', None, f'Equity {equity:.0%} reicht für Call {call} mit Puffer nicht.', equity)
    raise ValueError('Keine passende erlaubte Aktion')
