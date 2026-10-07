"""Heads-up no-limit Hold'em. All chip amounts are integer play-money chips."""
import random
from uuid import uuid4
from hand_eval import DECK, hand_rank, normalize_card


class Game:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.session_id = uuid4().hex
        self.last_ack = None
        self.stacks = [1000, 1000]
        self.hand_id = 0
        self.version = 0
        self.dealer = 1
        self.done = True
        self.board = []
        self.holes = [[], []]
        self.total = [0, 0]
        self.bets = [0, 0]
        self.log = []
        self.result = ''
        self.paused = True
        self.bot = {'status': 'Bereit – Übernahme starten', 'action': '', 'reason': ''}

    def _event(self, message):
        self.log.append(message)
        self.log = self.log[-30:]

    def _pay(self, player, amount):
        amount = min(amount, self.stacks[player])
        self.stacks[player] -= amount
        self.bets[player] += amount
        self.total[player] += amount

    def new_hand(self):
        if not self.done:
            raise ValueError('Die aktuelle Hand läuft noch')
        if min(self.stacks) == 0:
            raise ValueError('Übung beendet. Für neue Chips die Sitzung zurücksetzen.')
        self.hand_id += 1
        self.version += 1
        self.dealer = 1 - self.dealer
        self.deck = [r + s for r, s in DECK]
        self.rng.shuffle(self.deck)
        self.holes = [[self.deck.pop(), self.deck.pop()] for _ in range(2)]
        self.board = []
        self.total = [0, 0]
        self.bets = [0, 0]
        self.street = 'preflop'
        self.done = False
        self.result = ''
        self.showdown = False
        self.last_raise = 10
        self.pending = {0, 1}
        self._pay(self.dealer, 5)
        self._pay(1 - self.dealer, 10)
        self.turn = self.dealer
        self._event(f'Hand {self.hand_id} · Blinds 5 / 10')
        if self.stacks[self.turn] == 0:
            self.pending.discard(self.turn)
            self.turn = 1 - self.turn
        if all(s == 0 for s in self.stacks):
            self._runout()

    def legal(self, player):
        if self.done or self.turn != player or self.stacks[player] == 0:
            return {}
        current = max(self.bets)
        call = min(current - self.bets[player], self.stacks[player])
        actions = {'fold': None} if call else {'check': None}
        if call:
            actions['call'] = call
        maximum = min(self.bets[player] + self.stacks[player],
                      self.bets[1 - player] + self.stacks[1 - player])
        if maximum > current and self.stacks[1 - player] > 0:
            actions['raise'] = {'min': min(current + self.last_raise, maximum), 'max': maximum}
        return actions

    def act(self, player, action, amount=None):
        legal = self.legal(player)
        if action not in legal:
            raise ValueError('Aktion ist in diesem Zug nicht erlaubt')
        current = max(self.bets)
        name = 'Du' if player == 0 else 'Übungsgegner'
        if action == 'raise':
            limits = legal['raise']
            if type(amount) is not int or not limits['min'] <= amount <= limits['max']:
                raise ValueError('Raise liegt außerhalb der erlaubten Grenzen')
            self._pay(player, amount - self.bets[player])
            self.last_raise = max(self.last_raise, amount - current)
            self.pending = {1 - player}
            self._event(f'{name}: Raise auf {amount}')
        elif action == 'fold':
            self.version += 1
            self._award(1 - player, f'{name} foldet')
            return
        else:
            if action == 'call':
                self._pay(player, legal['call'])
            self.pending.discard(player)
            self._event(f'{name}: {action.capitalize()}' + (f' {legal[action]}' if action == 'call' else ''))
        self.version += 1
        self.pending = {p for p in self.pending if self.stacks[p] > 0}
        if not self.pending:
            if min(self.stacks) == 0:
                self._runout()
            elif self.street == 'river':
                self._showdown()
            else:
                self._next_street()
        else:
            self.turn = 1 - player

    def _next_street(self):
        count = 3 if self.street == 'preflop' else 1
        self.board.extend(self.deck.pop() for _ in range(count))
        self.street = {3: 'flop', 4: 'turn', 5: 'river'}[len(self.board)]
        self.bets = [0, 0]
        self.pending = {0, 1}
        self.last_raise = 10
        self.turn = 1 - self.dealer
        self._event(self.street.capitalize() + ': ' + ' '.join(self.board))

    def _refund(self):
        # Heads-up: only the matched part of an all-in can be won.
        larger = 0 if self.total[0] > self.total[1] else 1
        excess = self.total[larger] - self.total[1 - larger]
        self.stacks[larger] += excess
        self.total[larger] -= excess

    def _runout(self):
        while len(self.board) < 5:
            self.board.append(self.deck.pop())
        self.street = 'river'
        self._showdown()

    def _showdown(self):
        self.showdown = True
        self._refund()
        ranks = [hand_rank([normalize_card(c) for c in h + self.board]) for h in self.holes]
        if ranks[0] == ranks[1]:
            pot = sum(self.total)
            self.stacks[0] += pot // 2
            self.stacks[1] += pot // 2
            self.stacks[1 - self.dealer] += pot % 2
            self._finish('Split-Pot')
        else:
            self._award(0 if ranks[0] > ranks[1] else 1, 'Showdown')

    def _award(self, winner, reason):
        # Refund unmatched wagers on folds as well, preserving the same payout.
        self._refund()
        pot = sum(self.total)
        self.stacks[winner] += pot
        self._finish(f'{reason} · ' + ('Du gewinnst' if winner == 0 else 'Übungsgegner gewinnt') + f' {pot} Chips')

    def _finish(self, result):
        self.result = result
        self._event(result)
        self.done = True
        self.turn = None
        self.total = [0, 0]
        self.bets = [0, 0]

    def advance_opponent(self):
        """A simple practice opponent; only its own cards and public board are used."""
        from hand_eval import get_equity
        while not self.done and self.turn == 1:
            legal = self.legal(1)
            if not legal:
                self._runout()
                break
            equity = get_equity(self.holes[1], self.board) / 100
            odds = legal.get('call', 0) / max(sum(self.total) + legal.get('call', 0), 1)
            if 'raise' in legal and equity > 0.65 and self.rng.random() < 0.55:
                limits = legal['raise']
                target = max(limits['min'], max(self.bets) + max(10, sum(self.total) // 2))
                self.act(1, 'raise', min(target, limits['max']))
            elif 'check' in legal:
                self.act(1, 'check')
            elif equity >= odds + 0.08 or self.rng.random() < 0.15:
                self.act(1, 'call')
            else:
                self.act(1, 'fold')

    def public_state(self):
        """Never expose opponent hole cards before showdown, including folded hands."""
        return {
            'hand_id': self.hand_id, 'version': self.version,
            'decision_id': f'{self.session_id}:{self.hand_id}:{self.version}',
            'last_ack': self.last_ack,
            'my_cards': self.holes[0], 'opponent_cards': self.holes[1] if getattr(self, 'showdown', False) else [],
            'board': self.board, 'stacks': self.stacks, 'bets': self.bets,
            'pot': sum(self.total), 'street': getattr(self, 'street', 'preflop'),
            'dealer': self.dealer, 'is_my_turn': not self.done and self.turn == 0,
            'actions': self.legal(0), 'done': self.done, 'result': self.result,
            'paused': self.paused, 'bot': self.bot, 'log': self.log,
        }
