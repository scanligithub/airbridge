# test_runtime_session_protocol.py
# -*- coding: utf-8 -*-
import unittest
import uuid

from protocol import (
    ABFPFrame,
    FrameParser,
    FrameType,
    MAX_PAYLOAD_LEN,
)
from runtime_session_protocol import (
    SESSION_PROTOCOL_ID,
    SessionProtocolError,
    decode_session_message,
    encode_session_message,
    make_session_capabilities,
    make_session_hello,
)


class RuntimeSessionProtocolTests(unittest.TestCase):
    def test_hello_envelope_round_trip(self):
        hello = make_session_hello()
        encoded = encode_session_message(FrameType.SESSION_HELLO, hello)
        decoded = decode_session_message(FrameType.SESSION_HELLO, encoded)

        self.assertEqual(decoded["message_id"], hello["message_id"])
        self.assertEqual(
            decoded["payload"]["supported_protocols"],
            [SESSION_PROTOCOL_ID],
        )

    def test_capabilities_correlates_to_hello_message(self):
        hello = make_session_hello()
        capabilities = make_session_capabilities(hello["message_id"])
        encoded = encode_session_message(
            FrameType.SESSION_CAPABILITIES, capabilities
        )
        decoded = decode_session_message(
            FrameType.SESSION_CAPABILITIES, encoded
        )

        self.assertEqual(decoded["correlation_id"], hello["message_id"])
        self.assertEqual(decoded["payload"]["features"], ["handshake", "request.correlation", "request.deduplication"])
        self.assertEqual(decoded["payload"]["profiles"], [])

    def test_unknown_schema_version_is_rejected(self):
        hello = make_session_hello()
        hello["schema_version"] = 999
        with self.assertRaises(SessionProtocolError):
            encode_session_message(FrameType.SESSION_HELLO, hello)

    def test_invalid_message_id_is_rejected(self):
        hello = make_session_hello()
        hello["message_id"] = "not-a-uuid"
        with self.assertRaises(SessionProtocolError):
            encode_session_message(FrameType.SESSION_HELLO, hello)

    def test_capabilities_requires_correlation_id(self):
        capabilities = make_session_capabilities(str(uuid.uuid4()))
        capabilities["correlation_id"] = None
        with self.assertRaises(SessionProtocolError):
            encode_session_message(
                FrameType.SESSION_CAPABILITIES, capabilities
            )

    def test_wrong_frame_payload_shape_is_rejected(self):
        hello = make_session_hello()
        with self.assertRaises(SessionProtocolError):
            encode_session_message(FrameType.SESSION_CAPABILITIES, hello)

    def test_oversized_payload_is_rejected(self):
        hello = make_session_hello()
        hello["payload"]["extra"] = "x" * MAX_PAYLOAD_LEN
        with self.assertRaises(SessionProtocolError):
            encode_session_message(FrameType.SESSION_HELLO, hello)

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(SessionProtocolError):
            decode_session_message(FrameType.SESSION_HELLO, b"{not-json")

    def test_new_frame_types_round_trip_through_abfp(self):
        for frame_type in (
            FrameType.SESSION_HELLO,
            FrameType.SESSION_CAPABILITIES,
        ):
            envelope = (
                make_session_hello()
                if frame_type == FrameType.SESSION_HELLO
                else make_session_capabilities(str(uuid.uuid4()))
            )
            payload = encode_session_message(frame_type, envelope)
            packed = ABFPFrame(frame_type, 0xFFFF, payload).pack()
            frames = FrameParser().feed(packed)

            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].frame_type, frame_type)
            self.assertEqual(frames[0].seq_id, 0xFFFF)
            self.assertEqual(
                decode_session_message(frame_type, frames[0].payload)["message_id"],
                envelope["message_id"],
            )

    def test_legacy_frame_ids_remain_unchanged(self):
        self.assertEqual(FrameType.EDA_TASK_SUBMIT, 0x10)
        self.assertEqual(FrameType.EDA_APP_CLOSE, 0x14)
        self.assertEqual(FrameType.FS_REQUEST, 0x20)
        self.assertEqual(FrameType.FS_RESPONSE, 0x21)
        self.assertEqual(FrameType.SHELL_EXEC, 0x30)
        self.assertEqual(FrameType.SHELL_LIST_RESP, 0x35)


if __name__ == "__main__":
    unittest.main()
