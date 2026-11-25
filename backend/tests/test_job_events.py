import json
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.errors import AppError, ErrorCode
from app.routers import jobs
from app.queue import jobs as worker_jobs
from app.queue.enqueue import enqueue_itinerary
from app.schemas import Itinerary, TripRequest


class JobEventsTests(unittest.TestCase):
    def setUp(self):
        self.job_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.db = Mock()
        app = FastAPI()
        app.include_router(jobs.router)
        app.dependency_overrides[jobs.current_user] = lambda: SimpleNamespace(id=self.user_id)
        app.dependency_overrides[jobs.get_db] = lambda: self.db
        self.client = TestClient(app)

    def read_frames(self):
        response = self.client.get(f"/jobs/{self.job_id}/events")
        self.assertEqual(response.status_code, 200)
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_finished_itinerary_survives_evicted_redis_snapshot(self):
        itinerary = {"destination": "Bangkok"}
        job = jobs.Job(id=self.job_id, status=jobs.JobStatus.succeeded, user_id=self.user_id, result_record_id=42)
        record = SimpleNamespace(user_id=self.user_id, itinerary=itinerary, elapsed_seconds=2.0)
        self.db.get.side_effect = [job, record]
        redis = AsyncMock()
        redis.get.return_value = None

        with patch.object(jobs, "async_redis", return_value=redis):
            self.assertEqual(self.read_frames(), [{
                "kind": "done", "status": "succeeded", "record_id": 42,
                "itinerary": itinerary, "elapsed_seconds": 2.0,
            }])

    def test_terminal_event_between_snapshot_and_tail_is_not_skipped(self):
        job = jobs.Job(id=self.job_id, status=jobs.JobStatus.running, user_id=self.user_id)
        self.db.get.return_value = job
        redis = AsyncMock()
        emitted = False

        async def snapshot(key):
            nonlocal emitted
            emitted = True
            return b'{"kind":"snapshot","status":"running"}'

        async def tail(*args, **kwargs):
            return [(b"2-0" if emitted else b"1-0", {})]

        async def read(streams, **kwargs):
            if next(iter(streams.values())) == "1-0":
                return [(b"events", [(b"2-0", {b"d": b'{"kind":"done","status":"succeeded"}'})])]
            return []

        redis.get.side_effect = snapshot
        redis.xrevrange.side_effect = tail
        redis.xread.side_effect = read
        with patch.object(jobs, "async_redis", return_value=redis), patch.object(
            jobs, "_fresh_terminal_frame", return_value={"kind": "error"},
        ):
            self.assertEqual(self.read_frames()[-1]["kind"], "done")

    def test_stop_commits_before_notifying_redis_and_survives_a_redis_failure(self):
        job = jobs.Job(id=self.job_id, status=jobs.JobStatus.running, user_id=self.user_id, kind="plan")
        placeholder = SimpleNamespace()
        self.db.scalar.side_effect = [job, placeholder]
        redis = AsyncMock()
        redis.__aenter__.return_value = redis

        async def disconnect(*args, **kwargs):
            self.db.commit.assert_called_once()
            raise RedisConnectionError("redis offline")

        redis.set.side_effect = disconnect
        with patch.object(jobs, "verify_csrf"), patch.object(jobs, "async_redis", return_value=redis):
            response = self.client.post(f"/jobs/{self.job_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(placeholder.kind, "stopped")


class ItineraryCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_itinerary_is_published_before_telegram_delivery(self):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        trip = TripRequest(destination="Bangkok", start_date="2026-12-21", end_date="2026-12-23", budget=1500)
        job = jobs.Job(id=job_id, status=jobs.JobStatus.queued, user_id=user_id)
        job.payload = {"trip_request": trip.model_dump(mode="json")}
        user = SimpleNamespace(memory_opt_in=False, telegram_chat_id=1, id=user_id)
        message = SimpleNamespace(conversation_id=uuid.uuid4())
        db = MagicMock()
        db.__enter__.return_value = db
        db.get.side_effect = lambda model, _, **kwargs: {jobs.Job: job, jobs.User: user}.get(model)
        db.scalar.return_value = message
        db.flush.side_effect = lambda: setattr(db.add.call_args.args[0], "id", 42)
        redis = Mock()

        async def deliver(*args):
            frames = [json.loads(call.args[1]["d"]) for call in redis.xadd.call_args_list]
            self.assertEqual(frames[-1]["kind"], "done")
            self.assertEqual(frames[-1]["record_id"], 42)

        crew = SimpleNamespace(run_crew=Mock(return_value=(Itinerary(destination="Bangkok"), 2.0, 10)))
        with patch.dict(sys.modules, {"app.crew.crew": crew}), patch.object(
            worker_jobs, "SessionLocal", return_value=db,
        ), patch.object(
            worker_jobs, "get_settings", return_value=SimpleNamespace(
                groq_compact_planning=False, model="crew-test", rag_enabled=False,
                crew_process="sequential", crew_memory=False, quality_model=None,
            ),
        ), patch.object(worker_jobs, "create_completion_delivery", return_value=None), patch.object(
            worker_jobs, "deliver_itinerary_job", side_effect=deliver,
        ) as telegram:
            await worker_jobs.run_itinerary_job({"redis_raw": redis, "executor": None}, str(job_id))

        telegram.assert_awaited_once()


class ItineraryEnqueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = uuid.uuid4()
        self.conversation_id = uuid.uuid4()
        self.trip = TripRequest(destination="Bangkok", start_date="2026-12-21", end_date="2026-12-23", budget=1500)
        self.db = MagicMock()
        self.pool = AsyncMock()

    async def enqueue(self, reply_text=None):
        return await enqueue_itinerary(
            self.db, self.pool, trip_request=self.trip, previous_record_id=None,
            user_id=self.user_id, conversation_id=self.conversation_id, idempotency_key="same-send",
            reply_text=reply_text,
        )

    async def test_conversation_placeholder_is_committed_before_publish(self):
        job_id = uuid.uuid4()
        self.db.execute.return_value.scalar_one_or_none.return_value = job_id
        self.db.scalar.side_effect = [self.conversation_id, None, 4]

        async def publish(*args, **kwargs):
            self.db.commit.assert_called_once()
            placeholder = self.db.add.call_args.args[0]
            self.assertEqual((placeholder.job_id, placeholder.seq, placeholder.kind), (job_id, 5, "job"))

        self.pool.enqueue_job.side_effect = publish
        self.assertEqual(await self.enqueue("Building your itinerary."), (job_id, True))

    async def test_retry_publishes_the_same_queued_job_after_redis_failure(self):
        job_id = uuid.uuid4()
        self.db.execute.return_value.scalar_one_or_none.side_effect = [job_id, None]
        self.db.scalar.return_value = jobs.Job(
            id=job_id, status=jobs.JobStatus.queued, user_id=self.user_id, conversation_id=self.conversation_id,
        )
        self.pool.enqueue_job.side_effect = [ConnectionError("redis unavailable"), None]

        with self.assertRaises(ConnectionError):
            await self.enqueue()
        self.assertEqual(await self.enqueue(), (job_id, False))
        initial, retried = self.pool.enqueue_job.await_args_list
        self.assertEqual(initial, retried)
        self.assertEqual(retried.kwargs["_job_id"], f"crew:{job_id}")

    async def test_accepted_replay_never_republishes_a_finished_job(self):
        job_id = uuid.uuid4()
        self.db.execute.return_value.scalar_one_or_none.return_value = None
        self.db.scalar.return_value = jobs.Job(
            id=job_id, status=jobs.JobStatus.succeeded, user_id=self.user_id, conversation_id=self.conversation_id,
        )

        self.assertEqual(await self.enqueue(), (job_id, False))
        self.pool.enqueue_job.assert_not_awaited()

    async def test_key_from_another_conversation_does_not_reuse_that_job(self):
        self.db.execute.return_value.scalar_one_or_none.return_value = None
        self.db.scalar.return_value = jobs.Job(
            id=uuid.uuid4(), status=jobs.JobStatus.queued, user_id=self.user_id, conversation_id=uuid.uuid4(),
        )

        with self.assertRaises(AppError) as exc:
            await self.enqueue()
        self.assertEqual(exc.exception.code, ErrorCode.E_VALIDATION)
        self.pool.enqueue_job.assert_not_awaited()
