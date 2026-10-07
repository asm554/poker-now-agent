import random
import unittest
from hand_eval import detect_draws, get_equity, monte_carlo_equity, normalize_card
from practice.game import Game
from practice.strategy import decide


class EvaluationTests(unittest.TestCase):
    def test_three_way_board_split(self):
        self.assertAlmostEqual(monte_carlo_equity(['2c', '3d'], ['Ah', 'Kh', 'Qh', 'Jh', 'Th'], 2, 20), 1 / 3)

    def test_unicode_suits(self):
        self.assertEqual(normalize_card('10♥'), ('T', 'h'))
        self.assertEqual(normalize_card('A♠️'), ('A', 's'))

    def test_invalid_cards(self):
        for holes in ([], ['Ah'], ['Ah', 'Ah'], ['ZZ', 'Kh']):
            with self.subTest(holes=holes), self.assertRaises(ValueError):
                get_equity(holes)

    def test_invalid_board(self):
        with self.assertRaises(ValueError):
            get_equity(['Ah', 'Kh'], ['2c', '3d'])

    def test_no_river_draw(self):
        self.assertEqual(detect_draws(['Ah', 'Kh'], ['Qh', 'Jh', '2c', '7d', '9s']), [])

    def test_omaha_requires_two_hole_cards_for_flush_draw(self):
        self.assertNotIn('flush draw', detect_draws(['Ah', 'Ks', 'Qc', 'Jd'], ['2h', '5h', '8h']))
        self.assertIn('flush draw', detect_draws(['Ah', 'Kh', 'Qc', 'Jd'], ['2h', '5h', '8c']))

    def test_wheel_and_broadway_draws(self):
        self.assertIn('gutshot', detect_draws(['Ah', '2c'], ['3s', '4d', 'Kh']))
        self.assertIn('gutshot', detect_draws(['Ah', 'Kc'], ['Qs', 'Jd', '2h']))


class GameTests(unittest.TestCase):
    def test_blinds_and_heads_up_positions(self):
        game = Game(1)
        game.new_hand()
        self.assertEqual(game.turn, game.dealer)
        self.assertEqual(sum(game.total), 15)
        game.act(game.turn, 'call')
        game.act(game.turn, 'check')
        self.assertEqual(game.street, 'flop')
        self.assertEqual(game.turn, 1 - game.dealer)

    def test_hidden_cards(self):
        game = Game(2)
        game.new_hand()
        self.assertEqual(game.public_state()['opponent_cards'], [])
        game.act(game.turn, 'fold')
        self.assertEqual(game.public_state()['opponent_cards'], [])

    def test_invalid_raise_is_not_committed(self):
        game = Game(3)
        game.new_hand()
        before = (list(game.stacks), game.version)
        with self.assertRaises(ValueError):
            game.act(game.turn, 'raise', 11)
        self.assertEqual(before, (game.stacks, game.version))

    def test_all_in_and_conservation(self):
        game = Game(4)
        game.stacks = [30, 1970]
        game.new_hand()
        game.act(game.turn, 'raise', game.legal(game.turn)['raise']['max'])
        game.act(game.turn, 'call')
        self.assertTrue(game.done)
        self.assertEqual(len(game.board), 5)
        self.assertEqual(sum(game.stacks), 2000)

    def test_short_big_blind(self):
        game = Game(5)
        game.stacks = [1997, 3]
        game.new_hand()
        game.act(game.turn, 'check')
        self.assertTrue(game.done)
        self.assertEqual(sum(game.stacks), 2000)

    def test_randomized_legal_play_preserves_chips(self):
        rng = random.Random(123)
        for seed in range(80):
            game = Game(seed)
            for _ in range(15):
                if min(game.stacks) == 0:
                    break
                game.new_hand()
                turns = 0
                while not game.done:
                    legal = game.legal(game.turn)
                    action = rng.choice(list(legal))
                    amount = rng.randint(legal['raise']['min'], legal['raise']['max']) if action == 'raise' else None
                    game.act(game.turn, action, amount)
                    self.assertEqual(sum(game.stacks) + sum(game.total), 2000)
                    self.assertGreaterEqual(min(game.stacks), 0)
                    turns += 1
                    self.assertLess(turns, 200)
                self.assertEqual(sum(game.stacks), 2000)

    def test_strategy_returns_legal_action(self):
        for seed in range(10):
            game = Game(seed)
            game.new_hand()
            state = game.public_state()
            decision = decide(state)
            self.assertIn(decision.action, state['actions'])
            if decision.action == 'raise':
                limits = state['actions']['raise']
                self.assertLessEqual(limits['min'], decision.amount)
                self.assertLessEqual(decision.amount, limits['max'])


if __name__ == '__main__':
    unittest.main()
