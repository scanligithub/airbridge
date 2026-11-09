import unittest

from protocol import ABFPFrame, FrameParser, FrameType, MAX_PAYLOAD_LEN


class RawStreamProtocolTests(unittest.TestCase):
    def test_utf8_text_round_trip(self):
        payload = "puts \"hello\"\n中文测试".encode("utf-8")
        encoded = ABFPFrame(FrameType.RAW_STREAM, 7, payload).pack()
        parsed = FrameParser().feed(encoded)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].frame_type, FrameType.RAW_STREAM)
        self.assertEqual(parsed[0].payload, payload)

    def test_binary_payload_round_trip(self):
        payload = bytes(range(256))
        parser = FrameParser()
        wire = ABFPFrame(FrameType.RAW_STREAM, 8, payload).pack()
        result = []
        for offset in range(0, len(wire), 17):
            result.extend(parser.feed(wire[offset:offset + 17]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].payload, payload)

    def test_single_frame_limit_is_protocol_limit(self):
        self.assertEqual(MAX_PAYLOAD_LEN, 65000)


if __name__ == "__main__":
    unittest.main()