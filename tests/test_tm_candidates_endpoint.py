from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.routers import api


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._result


class _FakeDb:
    def __init__(self, segment, collections=()):
        self.segment = segment
        self.collections = list(collections)
        self.rolled_back = False

    def query(self, model):
        return _FakeQuery(self.collections if model is api.MemoryBase else self.segment)

    def rollback(self):
        self.rolled_back = True


class TMCandidatesEndpointTests(unittest.TestCase):
    def setUp(self):
        self.file_id = uuid4()
        self.segment = SimpleNamespace(id=uuid4(), sentence_id="s1", source_text="Source")
        self.bound_id = uuid4()
        self.file = SimpleNamespace(
            id=self.file_id,
            collection_id=self.bound_id,
            collection_ids_json="[]",
            tm_scope_mode="selected",
            tm_match_threshold=0.8,
            source_language="en-US",
            target_language="zh-CN",
        )
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(api, "get_file_record_model", return_value=self.file))
        stack.enter_context(patch.object(api, "_require_file_record_read_access"))
        self.matcher = stack.enter_context(patch.object(api, "get_tm_candidates_for_text", return_value=[]))

    def lookup(self, collections=(), **kwargs):
        return api.get_segment_tm_candidates(
            file_record_id=self.file_id,
            segment_ref=str(self.segment.id),
            db=_FakeDb(self.segment, collections),
            current_user=SimpleNamespace(id=uuid4(), role="admin"),
            **kwargs,
        )

    def test_omitting_scope_preserves_file_default(self):
        self.lookup()
        self.assertEqual(self.matcher.call_args.kwargs["collection_ids"], [self.bound_id])
        self.file.tm_scope_mode = "language_pair_all"
        self.lookup()
        self.assertIsNone(self.matcher.call_args.kwargs["collection_ids"])

    def test_selected_libraries_override_file_scope(self):
        self.file.tm_scope_mode = "language_pair_all"
        ids = [uuid4(), uuid4()]
        libraries = [SimpleNamespace(id=id_, source_language="en-US", target_language="zh-CN") for id_ in ids]
        app = FastAPI()
        app.add_api_route(
            "/file-records/{file_record_id}/segments/{segment_ref}/tm-candidates",
            api.get_segment_tm_candidates,
        )
        app.dependency_overrides[api.get_db] = lambda: _FakeDb(self.segment, libraries)
        app.dependency_overrides[api.get_current_user] = lambda: SimpleNamespace(id=uuid4(), role="admin")
        with TestClient(app) as client:
            response = client.get(
                f"/file-records/{self.file_id}/segments/{self.segment.id}/tm-candidates",
                params=[("collection_ids", str(id_)) for id_ in [*ids, ids[0]]],
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.matcher.call_args.kwargs["collection_ids"], ids)
        self.assertEqual(self.matcher.call_args.kwargs["similarity_threshold"], 0.8)
        self.assertEqual(self.file.collection_id, self.bound_id)

    def test_invalid_selected_libraries_are_rejected(self):
        missing = uuid4()
        wrong_pair = SimpleNamespace(id=uuid4(), source_language="en-US", target_language="fr-FR")
        for libraries, selection, status in [
            ([], [missing], 404),
            ([wrong_pair], [wrong_pair.id], 400),
        ]:
            with self.subTest(status=status), self.assertRaises(HTTPException) as raised:
                self.lookup(libraries, collection_ids=selection)
            self.assertEqual(raised.exception.status_code, status)
        self.matcher.assert_not_called()

    def test_tm_candidate_statement_timeout_returns_degraded_empty_result(self):
        db = _FakeDb(self.segment)
        self.matcher.side_effect = OperationalError(
            "select * from memory_entries",
            {},
            Exception("canceling statement due to statement timeout"),
        )

        response = api.get_segment_tm_candidates(
            file_record_id=self.file_id,
            segment_ref=str(self.segment.id),
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="admin"),
        )

        self.assertTrue(db.rolled_back)
        self.assertEqual(response["segment_id"], str(self.segment.id))
        self.assertEqual(response["candidates"], [])
        self.assertTrue(response["timed_out"])
        self.assertEqual(response["degraded_reason"], "statement_timeout")


if __name__ == "__main__":
    unittest.main()
