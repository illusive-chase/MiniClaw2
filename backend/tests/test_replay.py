from __future__ import annotations

import unittest

from miniclaw2.replay import LiveReplayBuffer, covered_by_replay


class LiveReplayBufferTest(unittest.TestCase):
    def test_buffers_until_replay_marks_live_ready(self) -> None:
        buffer = LiveReplayBuffer()

        self.assertIsNone(
            buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        )
        self.assertIsNone(buffer.push_live({"type": "text_delta", "text": "old", "seq": 2}))

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=2),
            [],
        )
        self.assertEqual(
            buffer.push_live({"type": "text_delta", "text": "new", "seq": 3}),
            {"type": "text_delta", "text": "new", "seq": 3},
        )

    def test_flushes_live_events_after_replayed_gap(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n1", "seq": 1})
        buffer.push_live({"type": "text_delta", "text": "later", "seq": 13})

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=12),
            [{"type": "text_delta", "text": "later", "seq": 13}],
        )

    def test_does_not_cover_other_node_events(self) -> None:
        buffer = LiveReplayBuffer()

        buffer.push_live({"type": "node_started", "node_id": "n2", "seq": 1})
        buffer.push_live({"type": "text_delta", "text": "other", "seq": 2})

        self.assertEqual(
            buffer.mark_live_ready(replay_node_id="n1", replayed_through_seq=99),
            [
                {"type": "node_started", "node_id": "n2", "seq": 1},
                {"type": "text_delta", "text": "other", "seq": 2},
            ],
        )


class CoveredByReplayTest(unittest.TestCase):
    def test_requires_matching_node_for_node_started(self) -> None:
        self.assertTrue(
            covered_by_replay(
                "n1",
                {"type": "node_started", "node_id": "n1", "seq": 1},
                "n1",
                1,
            )
        )
        self.assertFalse(
            covered_by_replay(
                "n2",
                {"type": "node_started", "node_id": "n2", "seq": 1},
                "n1",
                99,
            )
        )


if __name__ == "__main__":
    unittest.main()
